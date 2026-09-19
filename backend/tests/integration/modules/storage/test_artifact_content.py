"""ArtifactContent is the sole resolver for owned and source-backed bytes."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.core.config import _overlay
from app.core.errors import OperationError
from app.modules.sources.library_source import SourceContent, SourceEntry
from app.modules.storage import artifact_content
from tests.factories import detached_file


class TestRemoteArtifactContent:
    def test_materializes_remote_content_without_using_the_vault(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = b"remote NAS bytes"
        source_path = tmp_path / "source.stl"
        source_path.write_bytes(payload)
        row = detached_file(
            model_id=1,
            path="source://7/models/source.stl",
            original_filename="source.stl",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            is_external=True,
            external_library_id=4,
            source_key="models/source.stl",
        )

        class Source:
            @contextmanager
            def materialize(self, key: str, *, expected=None):
                assert key == "models/source.stl"
                assert expected == SourceEntry(key, len(payload))
                yield SourceContent(
                    source_path,
                    SourceEntry(source_path.name, source_path.stat().st_size),
                )

        monkeypatch.setattr(
            artifact_content,
            "source_for_file",
            lambda _file: (Source(), row.source_key),
        )
        monkeypatch.setattr(
            artifact_content,
            "get_backend",
            lambda: (_ for _ in ()).throw(AssertionError("vault backend used")),
        )

        with artifact_content.resolve(row).materialize() as resolved:
            observed = resolved.read_bytes()

        assert observed == payload

    def test_rejects_remote_content_that_changed_from_catalog_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source_path = tmp_path / "changed.stl"
        source_path.write_bytes(b"replacement")
        row = detached_file(
            model_id=1,
            path="source://7/models/changed.stl",
            original_filename="changed.stl",
            size_bytes=3,
            sha256=hashlib.sha256(b"old").hexdigest(),
            is_external=True,
            external_library_id=4,
            source_key="models/changed.stl",
        )

        class Source:
            @contextmanager
            def materialize(self, _key: str, *, expected=None):
                yield SourceContent(
                    source_path,
                    SourceEntry(source_path.name, source_path.stat().st_size),
                )

        monkeypatch.setattr(
            artifact_content,
            "source_for_file",
            lambda _file: (Source(), row.source_key),
        )

        with pytest.raises(artifact_content.ArtifactContentChangedError):
            with artifact_content.resolve(row).materialize():
                pass


class TestMountedArtifactContent:
    def test_mounted_content_is_stable_across_access_modes(
        self, tmp_path: Path
    ) -> None:
        payload = b"mounted NAS bytes"
        source_path = tmp_path / "mounted.stl"
        source_path.write_bytes(payload)
        row = detached_file(
            model_id=1,
            path=str(source_path),
            original_filename=source_path.name,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            is_external=True,
        )
        handle = artifact_content.resolve(row)

        with handle.materialize() as materialized:
            assert materialized.read_bytes() == payload
            assert materialized != source_path
        assert b"".join(handle.stream(chunk_size=3)) == payload

    @pytest.mark.parametrize("kind", ["missing", "symlink"])
    def test_rejects_missing_or_symlinked_mounted_content(
        self, tmp_path: Path, kind: str
    ) -> None:
        source_path = tmp_path / "unsafe.stl"
        if kind == "symlink":
            target = tmp_path / "target.stl"
            target.write_bytes(b"target")
            source_path.symlink_to(target)
        row = detached_file(
            model_id=1,
            path=str(source_path),
            original_filename=source_path.name,
            size_bytes=6,
            sha256=hashlib.sha256(b"target").hexdigest(),
            is_external=True,
        )

        with pytest.raises(artifact_content.ArtifactContentMissingError):
            with artifact_content.resolve(row).materialize():
                pass

    def test_rejects_a_mounted_file_whose_catalog_digest_is_stale(
        self, tmp_path: Path
    ) -> None:
        source_path = tmp_path / "stale.stl"
        source_path.write_bytes(b"new bytes")
        row = detached_file(
            model_id=1,
            path=str(source_path),
            original_filename=source_path.name,
            size_bytes=9,
            sha256=hashlib.sha256(b"old bytes").hexdigest(),
            is_external=True,
        )

        with pytest.raises(artifact_content.ArtifactContentChangedError):
            list(artifact_content.resolve(row).stream())


class TestManagedArtifactContent:
    class _Backend:
        def __init__(self, path: Path, *, exists: bool = True) -> None:
            self.path = path
            self.present = exists

        def exists(self, _key: str) -> bool:
            return self.present

        def stream_chunks(self, _key: str, _chunk_size: int):
            yield from ()

        def direct_path(self, _key: str):
            return self.path

        @contextmanager
        def local_path(self, _key: str):
            yield self.path

    def test_missing_managed_content_fails_before_stream_or_materialize(
        self, tmp_path: Path
    ) -> None:
        row = detached_file(
            model_id=1,
            path="vault/missing.stl",
            original_filename="missing.stl",
            size_bytes=1,
            sha256="0" * 64,
            is_external=False,
        )
        handle = artifact_content.resolve(
            row, backend=self._Backend(tmp_path / "missing", exists=False)
        )

        with pytest.raises(artifact_content.ArtifactContentMissingError):
            handle.stream()
        with pytest.raises(artifact_content.ArtifactContentMissingError):
            with handle.materialize():
                pass

    def test_empty_managed_object_preserves_empty_content(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.stl"
        path.write_bytes(b"")
        row = detached_file(
            model_id=1,
            path="vault/empty.stl",
            original_filename="empty.stl",
            size_bytes=0,
            sha256=hashlib.sha256(b"").hexdigest(),
            is_external=False,
        )
        handle = artifact_content.resolve(row, backend=self._Backend(path))

        assert list(handle.stream()) == []
        with handle.materialize() as materialized:
            assert materialized == path


class TestBoundedExternalContent:
    def test_capacity_denial_precedes_external_tempfile(self, tmp_path, monkeypatch):
        source = tmp_path / "bounded.gcode"
        source.write_bytes(b"bounded")
        row = detached_file(
            model_id=1,
            path=str(source),
            original_filename=source.name,
            size_bytes=7,
            sha256=hashlib.sha256(b"bounded").hexdigest(),
            is_external=True,
        )
        monkeypatch.setitem(_overlay, "storage_min_free_bytes", 10**18)
        monkeypatch.setattr(
            artifact_content.tempfile,
            "mkstemp",
            lambda *args, **kwargs: pytest.fail("temporary file allocated"),
        )

        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            with artifact_content.resolve(row).materialize():
                pass

    def test_changed_size_is_refused_before_opening_source(self, tmp_path, monkeypatch):
        source = tmp_path / "grown.gcode"
        source.write_bytes(b"unexpectedly larger content")
        row = detached_file(
            model_id=1,
            path=str(source),
            original_filename=source.name,
            size_bytes=1,
            sha256="a" * 64,
            is_external=True,
        )
        original_open = artifact_content.os.open

        def refuse_source_open(path, *args, **kwargs):
            if Path(path) == source:
                raise AssertionError(
                    "changed source opened before its size was checked"
                )
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(artifact_content.os, "open", refuse_source_open)
        with pytest.raises(artifact_content.ArtifactContentChangedError):
            with artifact_content.resolve(row).materialize():
                pytest.fail("changed source was materialized")


class TestManagedStreamCleanup:
    def test_early_close_releases_the_backend_reader(
        self, make_model, make_file, monkeypatch
    ):
        from app.modules.storage.storage_backend.runtime import get_backend

        backend = get_backend()
        backend.write_bytes(b"content", "cleanup.gcode")
        artifact = make_file(make_model(), path="cleanup.gcode")
        closed = []

        def chunks(_key, _size):
            try:
                yield b"first"
                yield b"second"
            finally:
                closed.append(True)

        monkeypatch.setattr(backend, "stream_chunks", chunks)
        reader = artifact_content.resolve(artifact, backend=backend).stream()
        assert next(reader) == b"first"
        reader.close()
        assert closed == [True]


@pytest.fixture
def managed_cache_content(make_model, make_file, tmp_path):
    from app.modules.storage.artifact_materializer import (
        ArtifactMaterializer,
        CachePolicy,
    )
    from app.modules.storage.materializer_runtime import (
        bind_materializer,
        get_materializer,
    )
    from tests.fakes.counting_remote_storage import CountingRemoteStorage

    original = get_materializer()
    source = tmp_path / "source.gcode"
    source.write_bytes(b"G28\nG1 X1\n")
    model = make_model("Remote cache model")
    row = make_file(
        model,
        path=str(source),
        filename="source.gcode",
        size_bytes=source.stat().st_size,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    backend = CountingRemoteStorage()
    cache = ArtifactMaterializer(
        tmp_path / "cache", lambda: CachePolicy(enabled=True, headroom_bytes=0)
    )
    bind_materializer(cache)
    yield artifact_content.resolve(row, backend=backend), cache, source, backend
    bind_materializer(original)


class TestManagedCacheContent:
    def test_cache_lookup_failure_streams_verified_source(
        self, managed_cache_content, monkeypatch
    ):
        handle, cache, source, backend = managed_cache_content
        monkeypatch.setattr(
            cache,
            "acquire",
            lambda _representation: (_ for _ in ()).throw(
                sqlite3.OperationalError("index unavailable")
            ),
        )

        assert b"".join(handle.stream()) == source.read_bytes()
        assert backend.bytes_read == source.stat().st_size

    def test_stream_cache_capacity_denial_preserves_source_delivery(
        self, managed_cache_content, monkeypatch
    ):
        from app.core.errors import ErrorKind

        handle, cache, source, backend = managed_cache_content
        monkeypatch.setattr(
            cache,
            "begin_fill",
            lambda _representation: (_ for _ in ()).throw(
                OperationError("cache_capacity", kind=ErrorKind.CAPACITY)
            ),
        )

        assert b"".join(handle.stream()) == source.read_bytes()
        assert backend.bytes_read == source.stat().st_size

    def test_stream_preserves_noncapacity_admission_failure(
        self, managed_cache_content, monkeypatch
    ):
        from app.core.errors import ErrorKind

        handle, cache, _, backend = managed_cache_content
        monkeypatch.setattr(
            cache,
            "begin_fill",
            lambda _representation: (_ for _ in ()).throw(
                OperationError("cache_unavailable", kind=ErrorKind.UNAVAILABLE)
            ),
        )

        with pytest.raises(OperationError, match="cache_unavailable"):
            handle.stream()
        assert backend.bytes_read == 0

    def test_midstream_cache_failure_preserves_source_delivery(
        self, managed_cache_content, monkeypatch
    ):
        from app.modules.storage.artifact_materializer import CacheUnavailable

        handle, cache, source, backend = managed_cache_content

        class UnavailableFill:
            def write(self, _chunk):
                raise CacheUnavailable("cache disappeared")

            def close(self):
                return None

        monkeypatch.setattr(
            cache, "begin_fill", lambda _representation: UnavailableFill()
        )

        assert b"".join(handle.stream()) == source.read_bytes()
        assert backend.bytes_read == source.stat().st_size
        assert cache.status()["entries"] == 0

    def test_completion_cache_failure_preserves_source_delivery(
        self, managed_cache_content, monkeypatch
    ):
        from app.modules.storage.artifact_materializer import CacheUnavailable

        handle, cache, source, backend = managed_cache_content

        class UnavailableFill:
            def write(self, _chunk):
                return None

            def complete(self):
                raise CacheUnavailable("cache disappeared")

            def close(self):
                return None

        monkeypatch.setattr(
            cache, "begin_fill", lambda _representation: UnavailableFill()
        )

        assert b"".join(handle.stream()) == source.read_bytes()
        assert backend.bytes_read == source.stat().st_size
        assert cache.status()["entries"] == 0

    def test_cache_capacity_denial_uses_separately_budgeted_temp(
        self, managed_cache_content
    ):
        from app.db.session import get_session_factory
        from app.modules.storage.capacity import CapacityManager, CapacityResource

        handle, cache, source, backend = managed_cache_content
        # The cache has an exhausted independent quota; the temporary filesystem
        # still goes through its real local-volume reservation and free-space probe.
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        cache.reserve = lambda token, root, size: manager.hold(
            f"cache:{token}",
            [CapacityResource.for_quota("cache-volume", size, 0, role="cache")],
        )
        with handle.materialize() as path:
            assert path.read_bytes() == source.read_bytes()
            assert path.parent != cache.root
            assert manager.reserved_bytes()
        assert backend.bytes_read == source.stat().st_size
        assert cache.status()["entries"] == 0
        assert manager.reserved_bytes() == {}

    def test_noncapacity_admission_error_remains_visible(self, managed_cache_content):
        from app.core.errors import ErrorKind, OperationError

        handle, cache, _, backend = managed_cache_content

        def unavailable_reservation(token, root, size):
            raise OperationError("reservation_failure", kind=ErrorKind.UNAVAILABLE)

        cache.reserve = unavailable_reservation
        with pytest.raises(OperationError, match="reservation_failure"):
            with handle.materialize():
                pytest.fail("unexpected admission error hidden")
        assert backend.bytes_read == 0

    def test_unavailable_index_falls_back_to_source(self, managed_cache_content):
        handle, cache, source, backend = managed_cache_content
        (cache.root / "index.sqlite3").write_bytes(b"broken index")
        with handle.materialize() as path:
            assert path.read_bytes() == source.read_bytes()
        assert backend.bytes_read == source.stat().st_size

    def test_fallback_does_not_bypass_capacity(
        self, managed_cache_content, monkeypatch
    ):
        from app.core.config import _overlay
        from app.core.errors import OperationError
        from app.modules.storage.artifact_materializer import CachePolicy

        handle, cache, _, backend = managed_cache_content
        cache.policy = lambda: CachePolicy(enabled=False)
        monkeypatch.setitem(_overlay, "storage_min_free_bytes", 2**62)
        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            with handle.materialize():
                pytest.fail("disk admission bypassed")
        assert backend.bytes_read == 0

    def test_authoritative_read_observes_source_replacement(
        self, managed_cache_content
    ):
        handle, _, source, backend = managed_cache_content
        with handle.materialize() as path:
            assert path.read_bytes() == b"G28\nG1 X1\n"
        source.write_bytes(b"G28\nG1 X2\n")
        assert b"".join(handle.stream(authoritative=True)) == b"G28\nG1 X2\n"
        assert backend.bytes_read == source.stat().st_size * 2

    def test_closes_primed_source_without_consuming_response(
        self, managed_cache_content
    ):
        handle, _, _, backend = managed_cache_content
        response = handle.stream()
        assert backend.open_readers == 1
        response.close()
        assert backend.open_readers == 0

    def test_closes_selected_cache_lease_before_first_read(self, managed_cache_content):
        handle, cache, _, _ = managed_cache_content
        with handle.materialize():
            pass
        response = handle.stream()
        assert cache.status()["leases"] == 1
        response.close()
        assert cache.status()["leases"] == 0

    def test_rejects_incorrect_remote_materialization(self, managed_cache_content):
        handle, cache, source, _ = managed_cache_content
        source.write_bytes(b"G28\nG1 X2\n")
        with pytest.raises(artifact_content.ArtifactContentChangedError):
            with handle.materialize():
                pytest.fail("incorrect Artifact exposed")
        assert cache.status()["entries"] == 0

    def test_external_sources_remain_outside_managed_cache(self, managed_cache_content):
        handle, cache, source, backend = managed_cache_content
        handle.file.is_external = True
        with artifact_content.resolve(handle.file).materialize() as path:
            assert path.read_bytes() == source.read_bytes()
        assert cache.status()["entries"] == 0
        assert backend.bytes_read == 0

    def test_proxy_reports_integrity_failure_after_streaming(
        self, managed_cache_content
    ):
        handle, cache, source, _ = managed_cache_content
        source.write_bytes(b"G28\nG1 X2\n")
        with pytest.raises(artifact_content.ArtifactContentChangedError):
            b"".join(handle.stream())
        assert cache.status()["entries"] == 0
        assert cache.status()["corruptions"] == 1

    def test_coalesces_concurrent_proxy_readers(self, managed_cache_content):
        from concurrent.futures import ThreadPoolExecutor

        handle, cache, source, backend = managed_cache_content
        first = handle.stream()
        with ThreadPoolExecutor(1) as pool:
            waiting = pool.submit(lambda: b"".join(handle.stream()))
            assert b"".join(first) == source.read_bytes()
            assert waiting.result(timeout=5) == source.read_bytes()
        assert backend.bytes_read == source.stat().st_size
        assert cache.status()["leases"] == 0

    def test_printer_upload_keeps_cache_path_during_clear(self, managed_cache_content):
        import asyncio

        from printstash_core.printers import (
            Capability,
            PrintArtifactFormat,
            ProviderCapabilities,
        )

        from app.modules.printing.printer_jobs import transfer_artifact

        handle, cache, source, backend = managed_cache_content
        with handle.materialize():
            pass
        uploaded = []

        class Provider:
            capabilities = ProviderCapabilities(
                supported=frozenset({Capability.UPLOAD}),
                accepted_print_formats=frozenset({PrintArtifactFormat.GCODE_TEXT}),
            )

            async def upload(self, path, remote_filename):
                cache.clear()
                uploaded.append(path.read_bytes())
                assert cache.status()["bytes"] == source.stat().st_size

        asyncio.run(
            transfer_artifact(
                backend, Provider(), handle.file, "part.gcode", start_print=False
            )
        )
        assert uploaded == [source.read_bytes()]
        assert backend.bytes_read == source.stat().st_size
        assert cache.status()["bytes"] == 0

    def test_archive_export_reuses_verified_artifact(
        self, managed_cache_content, db_session, make_user
    ):
        import zipfile

        from app.modules.ingestion.library_transfer import create_archive
        from app.modules.storage.storage_backend.runtime import (
            bind_backend,
            get_backend,
        )

        handle, _, source, backend = managed_cache_content
        previous = get_backend()
        bind_backend(backend)
        archive = None
        try:
            with handle.materialize():
                pass
            archive = create_archive(db_session, make_user(superuser=True))
            with zipfile.ZipFile(archive) as exported:
                bodies = [
                    exported.read(name)
                    for name in exported.namelist()
                    if name.endswith(".gcode")
                ]
            assert bodies == [source.read_bytes()]
            assert backend.bytes_read == source.stat().st_size
        finally:
            if archive is not None:
                archive.unlink(missing_ok=True)
            bind_backend(previous)


class TestArtifactContentContract:
    @pytest.mark.parametrize("present", [True, False])
    def test_checks_mounted_source_presence_without_using_the_vault(self, tmp_path, present):
        path = tmp_path / "mounted.stl"
        if present:
            path.write_bytes(b"source")
        row = detached_file(model_id=1, path=str(path), original_filename="mounted.stl",
            size_bytes=6, sha256=hashlib.sha256(b"source").hexdigest(), is_external=True)
        assert artifact_content.resolve(row).exists() is present
