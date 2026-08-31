from __future__ import annotations

import io
import threading
import time
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.db.models import (
    FileType,
    ThumbnailGeneration,
    ThumbnailGenerationState,
    ThumbnailRenderSlot,
)
from app.services.storage_backend import get_backend
from app.services.thumbnail_engine import ThumbnailResult, ThumbnailStrategy
from app.services.thumbnail_generations import (
    ThumbnailEnsureOutcome,
    ensure_thumbnail,
    publish_precomputed_thumbnail,
)
from tests.factories import build_file, build_model


@pytest.fixture(autouse=True)
def _use_threaded_db(threaded_hub_db: None) -> None:
    """Exercise thumbnail leases on SQLite connections that really contend."""


def _png() -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGBA", (80, 60), (100, 140, 210, 255)).save(output, format="PNG")
    return output.getvalue()


class _SuccessfulEngine:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, _request) -> ThumbnailResult:
        self.calls += 1
        return ThumbnailResult(
            image=_png(),
            geometry={
                "bbox_x_mm": None,
                "bbox_y_mm": None,
                "bbox_z_mm": None,
                "volume_mm3": None,
                "triangle_count": None,
            },
            strategy=ThumbnailStrategy.FULL,
            complete=True,
            failure_reason=None,
            duration_ms=4,
            peak_rss_bytes=1024,
        )


def test_ready_generation_is_reused_without_calling_the_renderer(
    db_session: Session,
) -> None:
    model = build_model(db_session)
    file_row = build_file(
        db_session,
        model,
        file_type=FileType.STL,
        filename="part.stl",
        sha256="a" * 64,
    )
    backend = get_backend()
    published = publish_precomputed_thumbnail(
        db_session,
        file_row,
        _png(),
        strategy=ThumbnailStrategy.FULL,
        complete=True,
        promote=True,
        backend=backend,
    )

    class RendererMustNotRun:
        def generate(self, request):
            raise AssertionError(f"cache hit rendered {request.path}")

    cached = ensure_thumbnail(
        db_session,
        file_row,
        backend=backend,
        engine=RendererMustNotRun(),  # type: ignore[arg-type]
    )

    assert published.outcome is ThumbnailEnsureOutcome.GENERATED
    assert cached.outcome is ThumbnailEnsureOutcome.CACHED
    assert file_row.thumbnail_path is not None
    assert "aaaaaaaaaaaa" in file_row.thumbnail_path
    assert backend.object_info(file_row.thumbnail_path) is not None


def test_running_generation_is_coalesced(
    db_session: Session,
) -> None:
    model = build_model(db_session)
    file_row = build_file(
        db_session,
        model,
        file_type=FileType.STL,
        filename="part.stl",
        sha256="b" * 64,
    )
    publish_precomputed_thumbnail(
        db_session,
        file_row,
        _png(),
        strategy=ThumbnailStrategy.FULL,
        complete=True,
        promote=True,
    )
    generation = db_session.exec(select(ThumbnailGeneration)).one()
    generation.state = ThumbnailGenerationState.RUNNING
    generation.storage_key = None
    generation.output_size_bytes = None
    generation.lease_token = "owned-by-another-worker"
    from datetime import timedelta

    from app.core.time import utcnow

    generation.lease_expires_at = utcnow() + timedelta(minutes=1)
    db_session.add(generation)
    db_session.commit()

    result = ensure_thumbnail(db_session, file_row)

    assert result.outcome is ThumbnailEnsureOutcome.COALESCED


def test_deterministic_failure_is_negative_cached(db_session: Session) -> None:
    model = build_model(db_session)
    file_row = build_file(
        db_session,
        model,
        file_type=FileType.OBJ,
        filename="broken.obj",
        sha256="c" * 64,
    )
    from app.services.thumbnail_generations import _get_or_create_generation

    generation = _get_or_create_generation(db_session, file_row)
    generation.state = ThumbnailGenerationState.FAILED
    generation.failure_reason = "no_geometry"
    db_session.add(generation)
    db_session.commit()

    result = ensure_thumbnail(db_session, file_row)

    assert result.outcome is ThumbnailEnsureOutcome.NEGATIVE_CACHED
    assert result.failure_reason == "no_geometry"


def test_expired_generation_lease_is_recovered(db_session: Session) -> None:
    from app.core.time import utcnow

    model = build_model(db_session)
    file_row = build_file(
        db_session,
        model,
        file_type=FileType.STL,
        filename="recover.stl",
        sha256="d" * 64,
    )
    backend = get_backend()
    backend.write_bytes(b"mesh", file_row.path)
    from app.services.thumbnail_generations import _get_or_create_generation

    generation = _get_or_create_generation(db_session, file_row)
    generation.state = ThumbnailGenerationState.RUNNING
    generation.lease_token = "dead-worker"
    generation.lease_expires_at = utcnow() - timedelta(seconds=1)
    db_session.add(generation)
    db_session.commit()
    engine = _SuccessfulEngine()

    result = ensure_thumbnail(db_session, file_row, backend=backend, engine=engine)

    assert result.outcome is ThumbnailEnsureOutcome.GENERATED
    assert engine.calls == 1
    db_session.refresh(generation)
    assert generation.state == ThumbnailGenerationState.READY
    assert generation.lease_token is None


def test_source_hash_change_publishes_a_new_variant(db_session: Session) -> None:
    model = build_model(db_session)
    file_row = build_file(
        db_session,
        model,
        file_type=FileType.STL,
        filename="changed.stl",
        sha256="e" * 64,
    )
    backend = get_backend()
    backend.write_bytes(b"mesh", file_row.path)
    first = publish_precomputed_thumbnail(
        db_session,
        file_row,
        _png(),
        strategy=ThumbnailStrategy.FULL,
        complete=True,
        promote=True,
        backend=backend,
    )
    first_key = file_row.thumbnail_path
    file_row.sha256 = "f" * 64
    db_session.add(file_row)
    db_session.commit()

    second = ensure_thumbnail(
        db_session,
        file_row,
        backend=backend,
        engine=_SuccessfulEngine(),
    )

    assert first.outcome is ThumbnailEnsureOutcome.GENERATED
    assert second.outcome is ThumbnailEnsureOutcome.GENERATED
    assert first_key is not None
    assert file_row.thumbnail_path is not None
    assert file_row.thumbnail_path != first_key
    assert "ffffffffffff" in file_row.thumbnail_path
    assert len(db_session.exec(select(ThumbnailGeneration)).all()) == 2


def test_worker_that_loses_its_lease_cannot_publish(db_session: Session) -> None:
    model = build_model(db_session)
    file_row = build_file(
        db_session,
        model,
        file_type=FileType.STL,
        filename="lease-lost.stl",
        sha256="1" * 64,
    )
    backend = get_backend()
    backend.write_bytes(b"mesh", file_row.path)

    class LeaseStealingEngine(_SuccessfulEngine):
        def generate(self, request) -> ThumbnailResult:
            generation = db_session.exec(select(ThumbnailGeneration)).one()
            generation.lease_token = "replacement-worker"
            db_session.add(generation)
            db_session.commit()
            return super().generate(request)

    result = ensure_thumbnail(
        db_session,
        file_row,
        backend=backend,
        engine=LeaseStealingEngine(),
    )

    assert result.outcome is ThumbnailEnsureOutcome.FAILED
    assert result.failure_reason == "lease_lost"
    assert file_row.thumbnail_path is None


def test_concurrent_requests_for_one_recipe_render_once(db_session: Session) -> None:
    model = build_model(db_session)
    file_row = build_file(
        db_session,
        model,
        file_type=FileType.STL,
        filename="coalesced.stl",
        sha256="2" * 64,
    )
    assert file_row.id is not None
    file_id = file_row.id
    backend = get_backend()
    backend.write_bytes(b"mesh", file_row.path)
    bind = db_session.get_bind()
    barrier = threading.Barrier(4)
    lock = threading.Lock()
    outcomes: list[ThumbnailEnsureOutcome] = []
    errors: list[BaseException] = []

    class SlowEngine(_SuccessfulEngine):
        def generate(self, request) -> ThumbnailResult:
            time.sleep(0.05)
            with lock:
                return super().generate(request)

    engine = SlowEngine()

    def worker() -> None:
        try:
            with Session(bind) as session:
                row = session.get(type(file_row), file_id)
                assert row is not None
                barrier.wait()
                result = ensure_thumbnail(
                    session,
                    row,
                    backend=backend,
                    engine=engine,
                )
                with lock:
                    outcomes.append(result.outcome)
        except BaseException as exc:  # noqa: BLE001 - collected across threads
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if errors:
        raise errors[0]
    assert engine.calls == 1
    assert outcomes.count(ThumbnailEnsureOutcome.GENERATED) == 1
    assert all(
        outcome
        in {
            ThumbnailEnsureOutcome.GENERATED,
            ThumbnailEnsureOutcome.CACHED,
            ThumbnailEnsureOutcome.COALESCED,
        }
        for outcome in outcomes
    )


def test_invalid_precomputed_image_is_recorded_as_a_failure(
    db_session: Session,
) -> None:
    model = build_model(db_session)
    file_row = build_file(db_session, model, filename="invalid.stl")

    result = publish_precomputed_thumbnail(
        db_session,
        file_row,
        b"not-an-image",
        strategy=ThumbnailStrategy.FULL,
        complete=True,
        promote=True,
    )

    assert result.outcome is ThumbnailEnsureOutcome.FAILED
    assert result.failure_reason == "invalid_source"


def test_forced_repair_retries_a_negative_cache_entry(db_session: Session) -> None:
    model = build_model(db_session)
    file_row = build_file(
        db_session,
        model,
        filename="forced.stl",
        sha256="3" * 64,
    )
    backend = get_backend()
    backend.write_bytes(b"mesh", file_row.path)
    from app.services.thumbnail_generations import _get_or_create_generation

    generation = _get_or_create_generation(db_session, file_row)
    generation.state = ThumbnailGenerationState.FAILED
    generation.failure_reason = "no_geometry"
    db_session.add(generation)
    db_session.commit()
    engine = _SuccessfulEngine()

    result = ensure_thumbnail(
        db_session,
        file_row,
        force=True,
        backend=backend,
        engine=engine,
    )

    assert result.outcome is ThumbnailEnsureOutcome.GENERATED
    assert engine.calls == 1


def test_busy_fleet_slot_returns_a_coalesced_result(db_session: Session) -> None:
    from app.core.time import utcnow

    model = build_model(db_session)
    file_row = build_file(db_session, model, filename="busy.stl", sha256="4" * 64)
    backend = get_backend()
    backend.write_bytes(b"mesh", file_row.path)
    db_session.add(
        ThumbnailRenderSlot(
            slot_number=1,
            lease_token="other-render",
            lease_expires_at=utcnow() + timedelta(minutes=1),
        )
    )
    db_session.commit()

    result = ensure_thumbnail(
        db_session,
        file_row,
        backend=backend,
        engine=_SuccessfulEngine(),
    )

    assert result.outcome is ThumbnailEnsureOutcome.COALESCED


def test_invalid_renderer_bytes_never_publish(db_session: Session) -> None:
    model = build_model(db_session)
    file_row = build_file(db_session, model, filename="bad-render.stl", sha256="5" * 64)
    backend = get_backend()
    backend.write_bytes(b"mesh", file_row.path)

    class InvalidImageEngine(_SuccessfulEngine):
        def generate(self, request) -> ThumbnailResult:
            result = super().generate(request)
            return ThumbnailResult(
                image=b"not-an-image",
                geometry=result.geometry,
                strategy=result.strategy,
                complete=result.complete,
                failure_reason=None,
                duration_ms=result.duration_ms,
                peak_rss_bytes=result.peak_rss_bytes,
            )

    result = ensure_thumbnail(
        db_session,
        file_row,
        backend=backend,
        engine=InvalidImageEngine(),
    )

    assert result.outcome is ThumbnailEnsureOutcome.FAILED
    assert result.failure_reason == "invalid_source"
    assert file_row.thumbnail_path is None


def test_republishing_identical_precomputed_bytes_reuses_the_variant(
    db_session: Session,
) -> None:
    model = build_model(db_session)
    file_row = build_file(db_session, model, filename="same.stl", sha256="6" * 64)
    backend = get_backend()
    first = publish_precomputed_thumbnail(
        db_session,
        file_row,
        _png(),
        strategy=ThumbnailStrategy.FULL,
        complete=True,
        promote=True,
        backend=backend,
    )
    first_key = file_row.thumbnail_path

    second = publish_precomputed_thumbnail(
        db_session,
        file_row,
        _png(),
        strategy=ThumbnailStrategy.FULL,
        complete=True,
        promote=True,
        backend=backend,
    )

    assert first.outcome is ThumbnailEnsureOutcome.GENERATED
    assert second.outcome is ThumbnailEnsureOutcome.GENERATED
    assert file_row.thumbnail_path == first_key


def test_invalid_ready_metadata_triggers_a_new_validation_pass(
    db_session: Session,
) -> None:
    model = build_model(db_session)
    file_row = build_file(db_session, model, filename="stale.stl", sha256="7" * 64)
    backend = get_backend()
    backend.write_bytes(b"mesh", file_row.path)
    publish_precomputed_thumbnail(
        db_session,
        file_row,
        _png(),
        strategy=ThumbnailStrategy.FULL,
        complete=True,
        promote=True,
        backend=backend,
    )
    generation = db_session.exec(select(ThumbnailGeneration)).one()
    assert generation.output_size_bytes is not None
    generation.output_size_bytes += 1
    db_session.add(generation)
    db_session.commit()
    engine = _SuccessfulEngine()

    result = ensure_thumbnail(
        db_session,
        file_row,
        backend=backend,
        engine=engine,
    )

    assert result.outcome is ThumbnailEnsureOutcome.GENERATED
    assert engine.calls == 1


def test_publication_exception_becomes_a_transient_storage_failure(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.thumbnail_generations as generations

    model = build_model(db_session)
    file_row = build_file(db_session, model, filename="storage.stl", sha256="8" * 64)
    backend = get_backend()
    backend.write_bytes(b"mesh", file_row.path)
    monkeypatch.setattr(
        generations,
        "_publish_encoded",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("storage offline")),
    )

    result = ensure_thumbnail(
        db_session,
        file_row,
        backend=backend,
        engine=_SuccessfulEngine(),
    )

    assert result.outcome is ThumbnailEnsureOutcome.FAILED
    assert result.failure_reason == "storage"


def test_precomputed_publication_exception_is_recorded(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.thumbnail_generations as generations

    model = build_model(db_session)
    file_row = build_file(db_session, model, filename="precomputed-storage.stl")
    monkeypatch.setattr(
        generations,
        "_publish_encoded",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("storage offline")),
    )

    result = publish_precomputed_thumbnail(
        db_session,
        file_row,
        _png(),
        strategy=ThumbnailStrategy.FULL,
        complete=True,
        promote=True,
    )

    assert result.outcome is ThumbnailEnsureOutcome.FAILED
    assert result.failure_reason == "storage"
