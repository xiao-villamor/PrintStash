"""Published sets retain usable images independently of primary derivatives."""

import hashlib
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import pytest
import trimesh
from sqlmodel import select

from app.core.time import utcnow
from app.db.models import ArtifactDerivative, DerivativeKind, DerivativeState, JobKind
from app.db.session import get_session_factory
from app.modules.derivatives import previews, producers, records
from app.modules.derivatives.kinds import group
from app.modules.derivatives.preview_source import PreviewSource
from app.modules.library.model_views.previews import resolve, summaries
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_utils import ownership_snapshot
from tests.factories.geometry import three_mf


@pytest.fixture
def artifact(stored, local_storage):
    return stored(
        "bracket.stl",
        trimesh.creation.box(extents=(10, 12, 14)).export(file_type="stl"),
    )


@pytest.fixture
def ready(db_session, artifact):
    result = previews.derive(artifact.id)
    assert result.kinds == {DerivativeKind.PREVIEWS: DerivativeState.READY}
    db_session.expire_all()
    return db_session.exec(
        select(ArtifactDerivative).where(
            ArtifactDerivative.file_id == artifact.id,
            ArtifactDerivative.kind == DerivativeKind.PREVIEWS,
        )
    ).one()


@pytest.fixture
def malformed_worker_image(monkeypatch):
    from app.modules.media import mesh_isolation
    from app.modules.media.preview_contracts import PreviewManifest

    original = mesh_isolation.prepared_worker_result

    @contextmanager
    def corrupt_image(*args, **kwargs):
        with original(*args, **kwargs) as (reply, directory):
            manifest = PreviewManifest.model_validate_json(
                reply.payload.rsplit(b"\n", 1)[-1]
            )
            first = manifest.views[0]
            bad = b"x" * first.size
            bundle = directory / "views.bin"
            bundle.write_bytes(bad + bundle.read_bytes()[first.size :])
            changed = first.model_copy(
                update={"sha256": hashlib.sha256(bad).hexdigest()}
            )
            manifest = manifest.model_copy(
                update={"views": (changed, *manifest.views[1:])}
            )
            yield replace(reply, payload=manifest.model_dump_json().encode()), directory

    monkeypatch.setattr(mesh_isolation, "prepared_worker_result", corrupt_image)


def _tamper(monkeypatch, change):
    """Let the real worker run, then alter what it hands to the publisher."""
    from app.modules.media import mesh_isolation
    from app.modules.media.preview_contracts import PreviewManifest

    original = mesh_isolation.prepared_worker_result

    @contextmanager
    def altered(*args, **kwargs):
        with original(*args, **kwargs) as (reply, directory):
            manifest = PreviewManifest.model_validate_json(
                reply.payload.rsplit(b"\n", 1)[-1]
            )
            bundle = directory / "views.bin"
            manifest = change(manifest, bundle, kwargs)
            yield replace(reply, payload=manifest.model_dump_json().encode()), directory

    monkeypatch.setattr(mesh_isolation, "prepared_worker_result", altered)


def _other_source(manifest, bundle, kwargs):
    return manifest.model_copy(update={"source_sha256": "b" * 64})


def _other_recipe(manifest, bundle, kwargs):
    return manifest.model_copy(update={"recipe": manifest.recipe + 1})


def _extra_bundle_byte(manifest, bundle, kwargs):
    bundle.write_bytes(bundle.read_bytes() + b"x")
    return manifest


def _flipped_image_byte(manifest, bundle, kwargs):
    data = bytearray(bundle.read_bytes())
    data[manifest.views[0].offset + 20] ^= 0xFF
    bundle.write_bytes(bytes(data))
    return manifest


def _noncanonical_image(manifest, bundle, kwargs):
    """A valid image whose bytes the canonical seam would not have kept."""
    import io

    from PIL import Image

    data = bundle.read_bytes()
    first = manifest.views[0]
    output = io.BytesIO()
    # Same frame and alpha, but not the WebP the canonical seam would keep.
    Image.open(io.BytesIO(data[: first.size])).save(output, "PNG")
    image = output.getvalue()
    rest = data[first.size :]
    bundle.write_bytes(image + rest)
    views = [
        first.model_copy(
            update={"size": len(image), "sha256": hashlib.sha256(image).hexdigest()}
        )
    ]
    position = len(image)
    for view in manifest.views[1:]:
        views.append(view.model_copy(update={"offset": position}))
        position += view.size
    return manifest.model_copy(update={"views": tuple(views), "size": position})


def _garbled_progress(manifest, bundle, kwargs):
    kwargs["on_chunk"](b"\n\xff:1:6\n")
    return manifest


class TestPreviewPublication:
    def test_rejects_invalid_advertised_image(
        self, db_session, artifact, malformed_worker_image
    ):
        assert (
            previews.derive(artifact.id).kinds[DerivativeKind.PREVIEWS]
            is DerivativeState.FAILED
        )
        db_session.expire_all()
        summary = summaries(db_session, [artifact.model_id])[artifact.model_id]
        assert summary.kind == "artifact"
        assert summary.state == "failed"
        assert summary.published is None
        assert (
            records.rows_for(db_session, artifact)[DerivativeKind.PREVIEWS].storage_key
            is None
        )

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            (_other_source, "invalid_source"),
            (_other_recipe, "invalid_source"),
            (_extra_bundle_byte, "invalid_source"),
            (_flipped_image_byte, "invalid_source"),
            (_noncanonical_image, "invalid_source"),
            (_garbled_progress, "worker_failed"),
        ],
        ids=[
            "source",
            "recipe",
            "bundle-size",
            "image-digest",
            "noncanonical-image",
            "garbled-progress",
        ],
    )
    def test_publishes_nothing_the_worker_cannot_vouch_for(
        self, db_session, artifact, monkeypatch, change, reason
    ):
        _tamper(monkeypatch, change)
        assert (
            previews.derive(artifact.id).kinds[DerivativeKind.PREVIEWS]
            is DerivativeState.FAILED
        )
        db_session.expire_all()
        row = records.rows_for(db_session, artifact)[DerivativeKind.PREVIEWS]
        assert (row.failure_reason, row.storage_key) == (reason, None)

    def test_does_not_rederive_a_ready_set(self, db_session, artifact, ready):
        assert previews.derive(artifact.id).kinds == {}
        db_session.expire_all()
        assert (
            records.rows_for(db_session, artifact)[DerivativeKind.PREVIEWS].storage_key
            == ready.storage_key
        )

    def test_yields_to_a_superseding_attempt(self, db_session, artifact, monkeypatch):
        def supersede(manifest, bundle, kwargs):
            # A reset or retry claims the subject while this batch renders.
            with get_session_factory().scoped_session() as session:
                file = session.get(type(artifact), artifact.id)
                records.reset(session, file, {DerivativeKind.PREVIEWS: 1})
                session.commit()
                records.begin(session, file, DerivativeKind.PREVIEWS, 1, now=utcnow())
                session.commit()
            return manifest

        _tamper(monkeypatch, supersede)
        assert previews.derive(artifact.id).kinds == {}
        db_session.expire_all()
        row = records.rows_for(db_session, artifact)[DerivativeKind.PREVIEWS]
        assert (row.state, row.storage_key) == (DerivativeState.RUNNING, None)

    def test_keeps_previous_recipe_images_during_replacement(
        self, db_session, artifact, ready, monkeypatch
    ):
        monkeypatch.setattr(
            "app.modules.library.model_views.previews.PREVIEWS_RECIPE", 2
        )
        records.begin(db_session, artifact, DerivativeKind.PREVIEWS, 2, now=utcnow())
        db_session.commit()
        summary = summaries(db_session, [artifact.model_id])[artifact.model_id]
        assert summary.state == "running"
        _, manifest = resolve(
            db_session,
            artifact.model_id,
            summary.published.manifest_url.rsplit("/", 1)[1],
        )
        assert manifest.recipe == 1
        assert len(manifest.views) == 6

    def test_publishes_known_camera_frames(self, db_session, artifact, ready):
        summary = summaries(db_session, [artifact.model_id])[artifact.model_id]
        _, manifest = resolve(
            db_session,
            artifact.model_id,
            summary.published.manifest_url.rsplit("/", 1)[1],
        )
        assert manifest.file_id == artifact.id
        assert [view.azimuth_degrees for view in manifest.views[:4]] == [
            -35,
            55,
            145,
            235,
        ]
        data = get_backend().read_bytes(ready.storage_key)
        for view in manifest.views:
            assert (
                hashlib.sha256(data[view.offset : view.offset + view.size]).hexdigest()
                == view.sha256
            )

    def test_preserves_primary_cover_while_pending(
        self, db_session, artifact, make_derivative
    ):
        producers.derive_mesh(artifact.id)
        db_session.expire_all()
        assert artifact.thumbnail_path is not None
        previous = get_backend().read_bytes(artifact.thumbnail_path)
        records.begin(db_session, artifact, DerivativeKind.PREVIEWS, 1, now=utcnow())
        db_session.commit()
        db_session.refresh(artifact)
        assert get_backend().read_bytes(artifact.thumbnail_path) == previous

    def test_keeps_unknown_cover_camera_explicit(self, db_session, artifact, ready):
        summary = summaries(db_session, [artifact.model_id])[artifact.model_id]
        assert summary.cover_camera == "unknown"
        assert all(view.role != "cover" for view in summary.published.views)

    def test_isolates_secondary_failure(self, db_session, stored, make_derivative):
        artifact = stored("broken.stl", b"not geometry")
        make_derivative(artifact, DerivativeKind.THUMBNAIL, storage_key="existing.webp")
        assert (
            previews.derive(artifact.id).kinds[DerivativeKind.PREVIEWS]
            is DerivativeState.FAILED
        )
        db_session.expire_all()
        rows = records.rows_for(db_session, artifact)
        assert rows[DerivativeKind.THUMBNAIL].state is DerivativeState.READY

    def test_preserves_valid_generation_during_replacement(
        self, db_session, artifact, ready
    ):
        previous = ready.storage_key
        records.begin(db_session, artifact, DerivativeKind.PREVIEWS, 1, now=utcnow())
        db_session.commit()
        summary = summaries(db_session, [artifact.model_id])[artifact.model_id]
        assert summary.state == "running"
        assert len(summary.published.views) == 6
        assert get_backend().read_bytes(previous)

    def test_preserves_valid_generation_after_failed_retry(
        self, db_session, artifact, ready
    ):
        begun = records.begin(
            db_session, artifact, DerivativeKind.PREVIEWS, 1, now=utcnow()
        )
        authority = records.attempt(db_session, artifact, begun)
        records.mark_failed(
            db_session, authority, "renderer_failed", now=utcnow(), deterministic=True
        )
        db_session.commit()
        records.reset(db_session, artifact, {DerivativeKind.PREVIEWS: 1})
        db_session.commit()
        summary = summaries(db_session, [artifact.model_id])[artifact.model_id]
        assert summary.state == "queued"
        assert summary.reason is None
        assert len(summary.published.views) == 6

    def test_cancels_optional_work_durably(self, db_session, artifact, make_derivative):
        make_derivative(artifact, DerivativeKind.THUMBNAIL)
        records.cancel(db_session, artifact, {DerivativeKind.PREVIEWS: 1}, now=utcnow())
        db_session.commit()
        source = PreviewSource(group(JobKind.DERIVATIVES_PREVIEWS))
        assert source.pending(db_session, now=utcnow(), limit=20) == []
        assert (
            records.rows_for(db_session, artifact)[DerivativeKind.THUMBNAIL].state
            is DerivativeState.READY
        )

    def test_owns_every_published_byte(self, db_session, ready):
        census = ownership_snapshot(db_session, discover=False)
        assert ready.storage_key in census.claimed_keys
        assert get_backend().object_info(ready.storage_key).size > 0

    def test_does_not_mix_revisions(self, db_session, artifact, ready, make_file):
        from app.db.models import Model

        newer = make_file(db_session.get(Model, artifact.model_id), filename="new.stl")
        summary = summaries(db_session, [artifact.model_id])[artifact.model_id]
        assert summary.file_id == newer.id
        assert summary.state == "pending"
        assert summary.published is None

    def test_hides_trashed_artifacts(self, db_session, artifact, ready):
        artifact.deleted_at = utcnow()
        db_session.add(artifact)
        db_session.commit()
        summary = summaries(db_session, [artifact.model_id])[artifact.model_id]
        assert summary.kind == "none"

    def test_withholds_views_rendered_from_replaced_bytes(
        self, db_session, artifact, ready
    ):
        # A linked Library rescan may replace an Artifact's content in place.
        artifact.sha256 = "b" * 64
        db_session.add(artifact)
        db_session.commit()
        summary = summaries(db_session, [artifact.model_id])[artifact.model_id]
        assert summary.state == "pending"
        assert summary.published is None

    def test_raises_on_a_corrupt_stored_manifest(self, db_session, artifact, ready):
        from pydantic import ValidationError

        ready.output_json = "{}"
        db_session.add(ready)
        db_session.commit()
        with pytest.raises(ValidationError):
            summaries(db_session, [artifact.model_id])

    def test_addresses_shared_delivery_routes(self, db_session, artifact, ready):
        root = "/api/v1/share/token"
        summary = summaries(db_session, [artifact.model_id], root=root)[
            artifact.model_id
        ]
        assert summary.published.manifest_url.startswith(root + "/previews/")
        assert all(
            view.url.startswith(summary.published.manifest_url + "/")
            for view in summary.published.views
        )
        with pytest.raises(ValueError, match="needs_one_model"):
            summaries(db_session, [artifact.model_id, 0], root=root)

    def test_applies_3mf_instance_placement(self, db_session, stored):
        mesh = trimesh.creation.box(extents=(10, 12, 14))
        file = stored(
            "placed.3mf",
            three_mf(
                meshes={1: mesh},
                assemblies={2: [(1, "1 0 0 0 1 0 0 0 1 10 20 30")]},
                build=((2, "1 0 0 0 1 0 0 0 1 50 0 0"),),
            ),
        )
        assert (
            previews.derive(file.id).kinds[DerivativeKind.PREVIEWS]
            is DerivativeState.READY
        )
        summary = summaries(db_session, [file.model_id])[file.model_id]
        _, manifest = resolve(
            db_session,
            file.model_id,
            summary.published.manifest_url.rsplit("/", 1)[1],
        )
        assert manifest.center == pytest.approx((60, 20, 30))


class TestPreviewAdmission:
    def test_favors_queued_primary_work(
        self, db_session, artifact, make_derivative, make_job
    ):
        make_derivative(artifact, DerivativeKind.THUMBNAIL)
        make_job(kind=JobKind.DERIVATIVES_MESH, subject="file/999")
        source = PreviewSource(group(JobKind.DERIVATIVES_PREVIEWS))
        assert source.pending(db_session, now=utcnow(), limit=20) == []

    def test_allows_progress_during_continuous_ingestion(
        self, db_session, artifact, make_derivative, make_job
    ):
        make_derivative(artifact, DerivativeKind.THUMBNAIL)
        make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject="file/999",
            created_at=utcnow() - timedelta(minutes=3),
        )
        source = PreviewSource(group(JobKind.DERIVATIVES_PREVIEWS))
        now = utcnow()
        assert source.pending(db_session, now=now, limit=20) == []
        items = source.pending(db_session, now=now + timedelta(minutes=3), limit=20)
        assert [item.subject_key for item in items] == [f"file/{artifact.id}"]
        assert items[0].priority.value == "backfill"

    def test_waits_for_the_primary_attempt(self, db_session, artifact):
        source = PreviewSource(group(JobKind.DERIVATIVES_PREVIEWS))
        assert source.pending(db_session, now=utcnow(), limit=20) == []

    def test_admits_only_one_secondary_batch(
        self, db_session, artifact, make_derivative, make_job
    ):
        make_derivative(artifact, DerivativeKind.THUMBNAIL)
        make_job(kind=JobKind.DERIVATIVES_PREVIEWS, subject="file/999")
        source = PreviewSource(group(JobKind.DERIVATIVES_PREVIEWS))
        assert source.pending(db_session, now=utcnow(), limit=20) == []


@pytest.fixture
def cached_remote(artifact, tmp_path):
    from app.modules.storage.artifact_materializer import (
        ArtifactMaterializer,
        CachePolicy,
    )
    from app.modules.storage.materializer_runtime import (
        bind_materializer,
        get_materializer,
    )
    from app.modules.storage.storage_backend.runtime import bind_backend
    from tests.fakes.counting_remote_storage import CountingRemoteStorage

    old_backend, old_cache = get_backend(), get_materializer()
    remote = CountingRemoteStorage()
    cache = ArtifactMaterializer(
        tmp_path / "cache", lambda: CachePolicy(enabled=True, headroom_bytes=0)
    )
    bind_backend(remote)
    bind_materializer(cache)
    try:
        yield remote, cache
    finally:
        bind_backend(old_backend)
        bind_materializer(old_cache)


class TestPreviewRemoteCache:
    def test_reuses_a_warm_remote_artifact(self, db_session, artifact, cached_remote):
        from app.modules.storage.artifact_content import resolve as resolve_artifact

        remote, _ = cached_remote
        with resolve_artifact(artifact).materialize() as source:
            assert hashlib.sha256(source.read_bytes()).hexdigest() == artifact.sha256
        initial = remote.bytes_read
        assert (
            previews.derive(artifact.id).kinds[DerivativeKind.PREVIEWS]
            is DerivativeState.READY
        )
        assert remote.bytes_read == initial

    def test_materializes_after_a_cache_miss(self, artifact, cached_remote):
        remote, _ = cached_remote
        assert (
            previews.derive(artifact.id).kinds[DerivativeKind.PREVIEWS]
            is DerivativeState.READY
        )
        assert remote.bytes_read == artifact.size_bytes

    def test_materializes_with_the_cache_disabled(self, artifact, cached_remote):
        from app.modules.storage.materializer_runtime import bind_materializer

        bind_materializer(None)
        remote, _ = cached_remote
        assert (
            previews.derive(artifact.id).kinds[DerivativeKind.PREVIEWS]
            is DerivativeState.READY
        )
        assert remote.bytes_read == artifact.size_bytes

    def test_materializes_after_host_cache_eviction(self, artifact, cached_remote):
        from app.modules.storage.artifact_content import resolve as resolve_artifact

        remote, cache = cached_remote
        with resolve_artifact(artifact).materialize() as source:
            assert source.is_file()
        before = remote.bytes_read
        cache.clear()
        assert (
            previews.derive(artifact.id).kinds[DerivativeKind.PREVIEWS]
            is DerivativeState.READY
        )
        assert remote.bytes_read - before == artifact.size_bytes


class TestPreviewLimits:
    def test_refuses_oversized_sources_without_invalidating_artifact(
        self, db_session, artifact
    ):
        from app.core.config import settings

        # VAULT_MESH_MAX_LOAD_MB, shared with every other mesh path.
        artifact.size_bytes = settings.mesh_max_load_mb * 1024 * 1024 + 1
        db_session.add(artifact)
        db_session.commit()
        assert (
            previews.derive(artifact.id).kinds[DerivativeKind.PREVIEWS]
            is DerivativeState.FAILED
        )
        db_session.expire_all()
        row = records.rows_for(db_session, artifact)[DerivativeKind.PREVIEWS]
        assert row.failure_reason == "resource_limit"
        assert artifact.deleted_at is None

    def test_reports_progress_between_published_views(self, artifact):
        observed = []
        assert (
            previews.derive(
                artifact.id, progress=lambda done, total: observed.append((done, total))
            ).kinds[DerivativeKind.PREVIEWS]
            is DerivativeState.READY
        )
        assert observed == [(index, 6) for index in range(1, 7)]


class TestPreviewLinkedSources:
    def test_uses_descriptor_pinned_mounted_bytes(
        self, tmp_path, make_external_library, make_model, make_file, local_storage
    ):
        content = trimesh.creation.box().export(file_type="stl")
        path = tmp_path / "mounted.stl"
        path.write_bytes(content)
        library = make_external_library(tmp_path, root_identity=None)
        artifact = make_file(
            make_model(),
            filename=path.name,
            external=True,
            path=str(path),
            external_library_id=library.id,
            source_key=path.name,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        assert (
            previews.derive(artifact.id).kinds[DerivativeKind.PREVIEWS]
            is DerivativeState.READY
        )
        assert path.read_bytes() == content
