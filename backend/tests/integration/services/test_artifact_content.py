"""ArtifactContent is the sole resolver for owned and source-backed bytes."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.services import artifact_content
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
            def materialize(self, key: str):
                assert key == "models/source.stl"
                yield source_path

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
            def materialize(self, _key: str):
                yield source_path

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

        @contextmanager
        def local_path(self, _key: str):
            yield self.path

        def presigned_download_url(self, _key: str, _filename: str) -> str:
            return "https://download.example.test/signed"

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

    def test_empty_managed_object_preserves_empty_content(
        self, tmp_path: Path
    ) -> None:
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

    def test_presigning_is_never_exposed_for_external_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        external = detached_file(
            model_id=1,
            path="/mnt/models/external.stl",
            original_filename="external.stl",
            size_bytes=1,
            sha256="0" * 64,
            is_external=True,
        )
        managed = detached_file(
            model_id=1,
            path="vault/managed.stl",
            original_filename="managed.stl",
            size_bytes=1,
            sha256="0" * 64,
            is_external=False,
        )
        backend = self._Backend(Path("unused"))
        monkeypatch.setattr(artifact_content, "get_backend", lambda: backend)

        assert artifact_content.presigned_download_url(external, "external.stl") is None
        assert artifact_content.presigned_download_url(
            managed, "managed.stl"
        ) == "https://download.example.test/signed"
