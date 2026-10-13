"""A bounded source window admits I/O without holding native compute credits."""

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlmodel import SQLModel, create_engine

from app.db.session import (
    SQLiteSessionFactory,
    _set_sqlite_pragmas,
    get_session_factory,
    override_session_factory,
)
from app.modules.media import source_preparation
from app.modules.storage.artifact_content import ArtifactHandle, resolve
from app.runtime import native_runtime, preparation_runtime
from app.runtime.native_admission import AdmissionTooLarge, Resources
from app.runtime.preparation_runtime import make_pools
from tests.factories import detached_file


@pytest.fixture
def threaded_source_db(tmp_path, _patch_engine):
    """File SQLite queues concurrent capacity writers using production pragmas."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'source-preparation.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    previous = get_session_factory()
    factory = SQLiteSessionFactory(engine)
    try:
        SQLModel.metadata.create_all(engine)
        override_session_factory(factory)
        yield factory
    finally:
        override_session_factory(previous)
        engine.dispose()


@pytest.fixture(autouse=True)
def _source_database_before_inputs(request, _patch_engine):
    # Autouse precedes handles/db_session; the explicit reset dependency ensures
    # the suite's default factory cannot replace this per-test override later.
    if "threaded_source_db" in request.fixturenames:
        request.getfixturevalue("threaded_source_db")


@pytest.fixture
def pools(tmp_path, monkeypatch):
    owner = make_pools(tmp_path / "prepared", tmp_path / "io")
    previous = preparation_runtime.bind_pools(owner)
    monkeypatch.setattr(source_preparation, "capacity", lambda: Resources(2, 256))
    try:
        yield owner
    finally:
        preparation_runtime.bind_pools(previous)


@pytest.fixture
def handles(tmp_path):
    result = []
    for index in range(2):
        payload = bytes([index + 1]) * 64
        source = tmp_path / f"source-{index}.stl"
        source.write_bytes(payload)
        result.append(
            resolve(
                detached_file(
                    model_id=1,
                    path=str(source),
                    original_filename=source.name,
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    is_external=True,
                )
            )
        )
    return tuple(result)


class TestSourcePreparation:
    @pytest.mark.parametrize("extra", [128, 129], ids=["at-limit", "one-byte-over"])
    def test_output_staging_shares_the_prepared_window(self, pools, handles, extra):
        if extra == 128:
            with source_preparation.reserve_sources((handles[0],), output_bytes=extra):
                permit = preparation_runtime.current_permit()
                assert permit is not None
                assert permit.resources == Resources(1, 256)
        else:
            with pytest.raises(AdmissionTooLarge):
                with source_preparation.reserve_sources(
                    (handles[0],), output_bytes=extra
                ):
                    pytest.fail("output exceeded prepared-byte capacity")

    @pytest.mark.parametrize(
        "value", [-1, True, 1.5], ids=["negative", "boolean", "fractional"]
    )
    def test_rejects_malformed_output_reservations(self, pools, handles, value):
        with pytest.raises(ValueError, match="output bytes"):
            with source_preparation.reserve_sources((handles[0],), output_bytes=value):
                pytest.fail("invalid output reservation admitted")
        assert list(pools.prepared.directory.glob("*.ticket")) == []

    def test_pair_is_reserved_before_either_copy(self, pools, handles, monkeypatch):
        original = ArtifactHandle.materialize
        observed = []

        @contextmanager
        def materialize(handle, **kwargs):
            permit = preparation_runtime.current_permit()
            assert permit is not None
            observed.append(permit.resources)
            assert native_runtime.current_permit() is None
            with original(handle, **kwargs) as path:
                yield path

        monkeypatch.setattr(ArtifactHandle, "materialize", materialize)
        with source_preparation.prepare_sources(handles) as paths:
            assert [path.read_bytes() for path in paths] == [
                bytes([1]) * 64,
                bytes([2]) * 64,
            ]
            assert all(
                path != Path(handle.file.path)
                for path, handle in zip(paths, handles, strict=True)
            )
        assert observed == [Resources(1, 256), Resources(1, 256)]
        assert all(not path.exists() for path in paths)

    def test_oversized_batch_refuses_before_materialization(
        self, pools, handles, monkeypatch
    ):
        monkeypatch.setattr(source_preparation, "capacity", lambda: Resources(2, 255))

        @contextmanager
        def unexpected(*_args, **_kwargs):
            pytest.fail("oversized batch started I/O")
            yield

        monkeypatch.setattr(ArtifactHandle, "materialize", unexpected)
        with pytest.raises(AdmissionTooLarge):
            with source_preparation.prepare_sources(handles):
                pytest.fail("oversized batch admitted")

    def test_prepared_bytes_bound_waiting_sources(
        self, pools, handles, monkeypatch, threaded_source_db, db_session
    ):
        factory = get_session_factory()
        monkeypatch.setattr(source_preparation, "capacity", lambda: Resources(2, 128))
        queued = threading.Event()
        copied = threading.Event()
        calls = 0

        def check():
            nonlocal calls
            if threading.current_thread() is not threading.main_thread():
                calls += 1
                if calls >= 3:
                    queued.set()

        monkeypatch.setattr(source_preparation, "checkpoint", check)

        def prepare_second():
            override_session_factory(factory)
            with source_preparation.prepare_sources((handles[1],)) as paths:
                copied.set()
                return paths[0].read_bytes()

        with ThreadPoolExecutor(1) as executor:
            with source_preparation.prepare_sources((handles[0],)):
                future = executor.submit(prepare_second)
                assert queued.wait(5)
                assert not copied.is_set()
            assert future.result(timeout=5) == bytes([2]) * 64

    def test_releases_io_while_native_work_retains_source_bytes(
        self, pools, handles, threaded_source_db, db_session
    ):
        factory = get_session_factory()
        copied = threading.Event()
        native_done = threading.Event()
        native_amount = Resources(1, 100)

        def second():
            override_session_factory(factory)
            with source_preparation.prepare_sources((handles[1],)) as paths:
                copied.set()
                with native_runtime.admit(
                    native_amount, native_amount, checkpoint=lambda: None
                ):
                    assert paths[0].read_bytes() == bytes([2]) * 64
                    native_done.set()

        with ThreadPoolExecutor(1) as executor:
            with source_preparation.prepare_sources((handles[0],)):
                with native_runtime.admit(
                    native_amount, native_amount, checkpoint=lambda: None
                ):
                    future = executor.submit(second)
                    assert copied.wait(5)
                    assert not native_done.is_set()
            future.result(timeout=5)
        assert native_done.is_set()

    def test_rejects_preparation_after_native_admission(self, pools, handles):
        amount = Resources(1, 100)
        with native_runtime.admit(amount, amount, checkpoint=lambda: None):
            with pytest.raises(RuntimeError, match="before acquiring native"):
                with source_preparation.prepare_sources(handles):
                    pytest.fail("reversed resource ordering was accepted")

    def test_rejects_duplicate_live_materialization(self, pools, handles):
        with source_preparation.reserve_sources(handles) as batch:
            with batch.materialize() as paths:
                with pytest.raises(RuntimeError, match="already materialized"):
                    with batch.materialize():
                        pytest.fail("batch doubled its live copies")
                assert all(path.exists() for path in paths)

    def test_expired_batch_cannot_materialize_again(self, pools, handles):
        with source_preparation.reserve_sources(handles) as batch:
            pass
        with pytest.raises(ValueError, match="closed"):
            with batch.materialize():
                pytest.fail("expired byte reservation started I/O")


class TestAbandonedSourcePreparation:
    def test_reclaims_abandoned_copy_before_reusing_credits(self, tmp_path, handles):
        import json
        import selectors
        import subprocess
        import sys

        from app.bootstrap.native_resources import configure
        from tests.paths import BACKEND_DIR

        source = Path(handles[0].file.path)
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.fakes.source_preparation_process",
                str(tmp_path),
                str(source),
            ],
            cwd=BACKEND_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        abandoned = None
        try:
            assert child.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                assert selector.select(10), "source copy did not become ready"
            line = child.stdout.readline()
            assert line, child.stderr.read() if child.stderr else "no stderr"
            abandoned = Path(json.loads(line)["path"])
            assert abandoned.is_file()
            child.kill()
            child.wait(timeout=5)
            configure(tmp_path)

            with source_preparation.reserve_sources((handles[0],)):
                assert not abandoned.exists()
            assert source.read_bytes() == bytes([1]) * 64
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
            if abandoned is not None:
                abandoned.unlink(missing_ok=True)
