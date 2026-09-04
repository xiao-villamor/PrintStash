"""Branch coverage for backup.verify_backup's archive-corruption checks —
unsafe/duplicate members, symlinks, bad manifests, missing/mismatched files,
and version incompatibility — beyond the happy path in test_backup_restore.py."""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import _overlay
from app.services import backup
from tests.integration._backup_harness import BackupEnv, seed_model_with_blob


def _verify_direct(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> "backup.BackupVerification":
    """Bypass discovery (_list_local_backups re-reads the manifest to find the
    backup by id) and validate *archive* directly — some corruptions here also
    break discovery, which isn't what these tests are checking."""
    monkeypatch.setattr(backup, "get_backup_archive_path", lambda _id: archive)
    return backup.verify_backup(_id_from(archive))


def _extract(archive: Path) -> tuple[dict[str, bytes], dict]:
    contents: dict[str, bytes] = {}
    manifest: dict = {}
    with gzip.open(archive, "rb") as gz, tarfile.open(fileobj=gz, mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            data = tar.extractfile(member).read()
            contents[member.name] = data
            if member.name == "manifest.json":
                manifest = json.loads(data.decode("utf-8"))
    return contents, manifest


def _write(
    archive: Path, contents: dict[str, bytes], *, extra_symlink: str | None = None
) -> None:
    with gzip.open(archive, "wb") as gz, tarfile.open(fileobj=gz, mode="w:") as tar:
        for name, data in contents.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if extra_symlink:
            info = tarfile.TarInfo(name=extra_symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "manifest.json"
            tar.addfile(info)


def _id_from(archive: Path) -> str:
    return archive.name.removesuffix(".tar.gz").rsplit("-", 1)[-1]


def _stream_archive(first_name: str, payload: bytes) -> io.BytesIO:
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb") as compressed:
        with tarfile.open(fileobj=compressed, mode="w:") as archive:
            info = tarfile.TarInfo(first_name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    output.seek(0)
    return output


def _fresh_archive(env: BackupEnv) -> tuple[Path, dict[str, bytes], dict]:
    seed_model_with_blob(env, name="Verified", content=b"solid verified\n")
    meta = backup.create_backup()
    archive = Path(meta.path)
    contents, manifest = _extract(archive)
    return archive, contents, manifest


class TestBackupProviderIdentity:
    def test_opendal_webdav_provider_ref_pins_destination_identity(self) -> None:
        from app.services.storage_opendal import OpenDALStorageBackend
        from app.services.storage_ownership import provider_ref_for_backend
        from app.services.storage_providers import TransportKind, TransportSpec

        def make(
            endpoint: str,
            *,
            provider: str = "webdav",
            root: str = "vault-data",
            password: str = "one",
        ):
            return OpenDALStorageBackend(
                TransportSpec(
                    kind=TransportKind.WEBDAV,
                    provider=provider,
                    namespace=f"webdav/{root}",
                    options={
                        "endpoint_url": endpoint,
                        "username": "operator",
                        "password": password,
                        "root": root,
                    },
                )
            )

        original = provider_ref_for_backend(make("https://Dav.Example.test/base/"))
        assert original == provider_ref_for_backend(
            make("https://dav.example.test/base", password="rotated")
        )
        assert original != provider_ref_for_backend(
            make("https://other.example.test/base/")
        )
        assert original != provider_ref_for_backend(
            make("https://dav.example.test/base/", provider="nextcloud")
        )
        assert original != provider_ref_for_backend(
            make("https://dav.example.test/base/", root="other-root")
        )


class TestBackupManifestStream:
    def test_nonmanifest_first_member_is_not_discovered(self) -> None:
        body = _stream_archive("db.sqlite3", b"database")

        assert backup._read_manifest_from_stream(body) is None

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(b"not-json", id="invalid-json"),
            pytest.param(b"[]", id="nonobject-json"),
        ],
    )
    def test_malformed_manifest_is_not_discovered(self, payload: bytes) -> None:
        body = _stream_archive("manifest.json", payload)

        assert backup._read_manifest_from_stream(body) is None

    def test_unreadable_journal_directory_keeps_cache_pinned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_glob(_path: Path, _pattern: str):
            raise OSError("journal directory unavailable")

        monkeypatch.setattr(Path, "glob", fail_glob)

        assert backup._cache_path_pinned_by_restore_journal("cache") is True

    def test_opendal_webdav_provider_ref_rejects_secret_endpoint_components(
        self,
    ) -> None:
        from app.services.storage_opendal import OpenDALStorageBackend
        from app.services.storage_ownership import provider_ref_for_backend
        from app.services.storage_providers import TransportKind, TransportSpec

        for endpoint in (
            "https://operator:secret@dav.example.test/base",
            "https://dav.example.test/base?token=secret",
            "https://dav.example.test/base#fragment",
        ):
            backend = OpenDALStorageBackend(
                TransportSpec(
                    kind=TransportKind.WEBDAV,
                    provider="webdav",
                    namespace="webdav/vault-data",
                    options={
                        "endpoint_url": endpoint,
                        "username": "operator",
                        "password": "secret",
                        "root": "vault-data",
                    },
                )
            )
            with pytest.raises(ValueError, match="storage_provider_endpoint_invalid"):
                provider_ref_for_backend(backend)

    def test_opendal_sftp_provider_ref_pins_destination_identity(
        self,
    ) -> None:
        from app.services.storage_opendal import OpenDALStorageBackend
        from app.services.storage_ownership import provider_ref_for_backend
        from app.services.storage_providers import TransportKind, TransportSpec

        def make(
            host: str = "nas.example.test",
            port: int = 22,
            root: str = "vault-data",
            password: str = "one",
        ):
            return OpenDALStorageBackend(
                TransportSpec(
                    kind=TransportKind.SFTP,
                    provider="sftp",
                    namespace=f"sftp/{root}",
                    options={
                        "host": host,
                        "port": port,
                        "username": "operator",
                        "root": root,
                        "host_key": "ssh-ed25519 AAAA",
                        "password": password,
                    },
                )
            )

        original = provider_ref_for_backend(make())
        assert original == provider_ref_for_backend(make(password="rotated"))
        assert original != provider_ref_for_backend(make(host="other.example.test"))
        assert original != provider_ref_for_backend(make(port=2222))
        assert original != provider_ref_for_backend(make(root="other-root"))

    def test_endpoint_normalization_excludes_secret_components(
        self,
    ) -> None:
        assert backup._normalize_provider_endpoint("HTTPS://Example.COM:443/") == (
            "https://example.com"
        )
        with pytest.raises(ValueError, match="backup_s3_endpoint_invalid"):
            backup._normalize_provider_endpoint("https://user:secret@example.com")
        with pytest.raises(ValueError, match="backup_s3_endpoint_invalid"):
            backup._normalize_provider_endpoint("https://example.com/?token=secret")

    def test_provider_ref_depends_only_on_destination_identity(
        self,
    ) -> None:
        original = SimpleNamespace(
            backend_name="s3",
            provider_id="s3",
            transport="s3",
            _endpoint_url="https://S3.Example.com:443/",
            _region="us-east-1",
            _bucket="vault-a",
        )
        rotated = SimpleNamespace(**{**vars(original), "access_key": "rotated"})
        changed = SimpleNamespace(
            **{**vars(original), "_endpoint_url": "https://other.example.com"}
        )
        from app.services.storage_ownership import provider_ref_for_backend

        assert provider_ref_for_backend(
            original, namespace="vault-a/data"
        ) == provider_ref_for_backend(rotated, namespace="vault-a/data")
        assert provider_ref_for_backend(
            original, namespace="vault-a/data"
        ) != provider_ref_for_backend(changed, namespace="vault-a/data")

    def test_provider_ref_changes_when_region_changes(self) -> None:
        from app.services.storage_ownership import provider_ref_for_backend

        original = SimpleNamespace(
            backend_name="s3",
            provider_id="minio",
            transport="s3",
            _endpoint_url="https://s3.example.com",
            _region="us-east-1",
            _addressing_style="path",
        )
        changed = SimpleNamespace(**{**vars(original), "_region": "eu-west-1"})

        assert provider_ref_for_backend(
            original, namespace="bucket/prefix"
        ) != provider_ref_for_backend(changed, namespace="bucket/prefix")

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://user@example.com",
            "https://example.com/path?token=secret",
            "https://example.com/path#fragment",
            "https://[2001:db8::1]:bad",
        ],
    )
    def test_rejects_provider_endpoints_that_could_not_be_pinned(
        self, endpoint: str
    ) -> None:
        with pytest.raises(ValueError, match="backup_s3_endpoint_invalid"):
            backup._normalize_provider_endpoint(endpoint)

    def test_endpoint_normalization_preserves_canonical_identity(self) -> None:
        assert backup._normalize_provider_endpoint("HTTPS://Example.COM:443/") == (
            "https://example.com"
        )
        assert backup._normalize_provider_endpoint("http://[2001:DB8::1]:80/") == (
            "http://[2001:db8::1]"
        )
        assert backup._normalize_provider_endpoint("http://[2001:db8::1]") == (
            "http://[2001:db8::1]"
        )

    def test_provider_ref_equates_default_port_ipv6_spellings(self) -> None:
        from app.services.storage_ownership import provider_ref_for_backend

        explicit = SimpleNamespace(
            backend_name="s3",
            provider_id="minio",
            transport="s3",
            _endpoint_url="HTTPS://[2001:DB8::1]:443/",
            _region="us-east-1",
            _addressing_style="path",
        )
        implicit = SimpleNamespace(
            backend_name="s3",
            provider_id="minio",
            transport="s3",
            _endpoint_url="https://[2001:db8::1]",
            _region="us-east-1",
            _addressing_style="path",
        )

        assert provider_ref_for_backend(
            explicit, namespace="bucket/prefix"
        ) == provider_ref_for_backend(implicit, namespace="bucket/prefix")

    def test_provider_ref_includes_addressing_style_but_never_bucket_or_credentials(
        self,
    ) -> None:
        from app.services.storage_ownership import provider_ref_for_backend

        base = SimpleNamespace(
            backend_name="s3",
            provider_id="minio",
            transport="s3",
            _endpoint_url="https://S3.Example.com:443/",
            _region="us-east-1",
            _addressing_style="path",
            _bucket="vault-a",
            access_key="one",
            secret_key="secret-one",
        )
        changed_style = SimpleNamespace(
            **{**vars(base), "_addressing_style": "virtual"}
        )
        changed_bucket = SimpleNamespace(**{**vars(base), "_bucket": "vault-b"})
        rotated = SimpleNamespace(
            **{**vars(base), "access_key": "two", "secret_key": "secret-two"}
        )

        original = provider_ref_for_backend(base, namespace="vault-a/data")
        assert original != provider_ref_for_backend(
            changed_style, namespace="vault-a/data"
        )
        assert original == provider_ref_for_backend(
            changed_bucket, namespace="vault-a/data"
        )
        assert original == provider_ref_for_backend(rotated, namespace="vault-a/data")

    def test_source_ref_pins_complete_object_identity(self) -> None:
        first = backup._source_ref(
            location="s3", provider_ref="a" * 64, namespace="bucket/prefix", path="k"
        )
        assert first != backup._source_ref(
            location="s3", provider_ref="b" * 64, namespace="bucket/prefix", path="k"
        )

    def test_source_ref_changes_for_each_locator_component(self) -> None:
        base = {
            "location": "s3",
            "provider_ref": "a" * 64,
            "namespace": "bucket/prefix",
            "path": "k",
        }
        variants = (
            {**base, "location": "local"},
            {**base, "namespace": "other-prefix"},
            {**base, "path": "other-key"},
            {**base, "provider_ref": "b" * 64},
        )

        refs = {backup._source_ref(**candidate) for candidate in (base, *variants)}

        assert len(refs) == 5


class TestRestoreJournalStateMachine:
    def test_new_journal_binds_current_provider_to_started_lifecycle_binding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = SimpleNamespace(
            backend_name="local", provider_id="local", transport="local"
        )
        monkeypatch.setattr(backup, "get_backend", lambda: backend)
        path = tmp_path / ".restore-provider-bound.journal"

        state = backup._prepare_restore_journal(
            path,
            backup_id="provider-bound",
            archive_sha256="a" * 64,
            blobs=[],
            operation_nonce="b" * 64,
        )

        provider_ref = state.started["provider_ref"]
        assert isinstance(provider_ref, str)
        assert backup._journal_binding(state.started) == {
            "backup_id": "provider-bound",
            "operation_nonce": "b" * 64,
            "archive_sha256": "a" * 64,
            "provider_ref": provider_ref,
        }
        assert (
            json.loads(path.read_text().splitlines()[0])["provider_ref"] == provider_ref
        )

    def test_prepare_rejects_a_resumed_journal_bound_to_another_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = SimpleNamespace(
            backend_name="local", provider_id="local", transport="local"
        )
        monkeypatch.setattr(backup, "get_backend", lambda: backend)
        path = tmp_path / ".restore-provider-switch.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 2,
                    "backup_id": "provider-switch",
                    "archive_sha256": "a" * 64,
                    "operation_nonce": "b" * 64,
                    "backend": "local",
                    "namespaces": [],
                    "provider_ref": "c" * 64,
                }
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_mismatch"
        ):
            backup._prepare_restore_journal(
                path,
                backup_id="provider-switch",
                archive_sha256="a" * 64,
                blobs=[],
                operation_nonce="b" * 64,
            )

    def test_v1_journal_is_not_resumed_against_a_remote_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = SimpleNamespace(backend_name="s3", provider_id="s3", transport="s3")
        monkeypatch.setattr(backup, "get_backend", lambda: backend)
        path = tmp_path / ".restore-remote-v1.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 1,
                    "backup_id": "remote-v1",
                    "archive_sha256": "a" * 64,
                    "backend": "s3",
                    "namespaces": [],
                }
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_mismatch"
        ):
            backup._prepare_restore_journal(
                path,
                backup_id="remote-v1",
                archive_sha256="a" * 64,
                blobs=[],
            )

    def test_restore_rejects_a_provider_switch_before_discovery_or_mutation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.storage_ownership import provider_ref_for_backend

        old_backend = SimpleNamespace(
            backend_name="s3",
            provider_id="s3",
            transport="s3",
            _endpoint_url="https://old.example.test",
            _region="us-east-1",
            _addressing_style="path",
        )
        new_backend = SimpleNamespace(
            **{**vars(old_backend), "_endpoint_url": "https://new.example.test"}
        )
        old_provider_ref = provider_ref_for_backend(
            old_backend, namespace="bucket/data"
        )
        path = tmp_path / ".restore-provider-switch.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 2,
                    "backup_id": "provider-switch",
                    "archive_sha256": "a" * 64,
                    "operation_nonce": "b" * 64,
                    "backend": "s3",
                    "namespaces": ["bucket/data"],
                    "provider_ref": old_provider_ref,
                }
            )
            + "\n"
        )
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)
        monkeypatch.setattr(backup, "get_backend", lambda: new_backend)
        monkeypatch.setattr(
            backup, "_require_database_backup_support", lambda **_: None
        )
        discovered = False

        def fail_discovery(*_args: object, **_kwargs: object) -> None:
            nonlocal discovered
            discovered = True
            raise AssertionError("discovery must not run")

        monkeypatch.setattr(backup, "get_backup", fail_discovery)
        backup._restore_gate.set()
        try:
            with pytest.raises(
                backup.RestoreConflictError, match="restore_storage_provider_changed"
            ):
                backup.restore_backup("provider-switch")
        finally:
            backup._restore_gate.clear()

        assert discovered is False

    def test_rejects_complete_before_database_active(self, tmp_path: Path) -> None:
        binding = {
            "backup_id": "complete-too-early",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        path = tmp_path / ".restore-complete-too-early.journal"
        path.write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {"event": "started", "version": 2, **binding},
                    {"event": "complete", **binding},
                )
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    @pytest.mark.parametrize(
        "field",
        [
            pytest.param("backup_id", id="backup-id"),
            pytest.param("operation_nonce", id="operation-nonce"),
            pytest.param("archive_sha256", id="archive-hash"),
        ],
    )
    def test_rejects_complete_with_mismatched_identity(
        self, tmp_path: Path, field: str
    ) -> None:
        binding = {
            "backup_id": "identity-bound",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        mismatched = dict(binding)
        mismatched[field] = {
            "backup_id": "different-backup",
            "archive_sha256": "c" * 64,
            "operation_nonce": "d" * 64,
        }[field]
        path = tmp_path / ".restore-identity-bound.journal"
        path.write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {"event": "started", "version": 2, **binding},
                    {"event": "database_swap_intent", **binding},
                    {"event": "database_active", **binding},
                    {"event": "complete", **mismatched},
                )
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_rejects_blob_lifecycle_after_swap_intent(self, tmp_path: Path) -> None:
        started = {
            "event": "started",
            "version": 2,
            "backup_id": "state-machine",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        binding = {
            "backup_id": "state-machine",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        swap = {"event": "database_swap_intent", **binding}
        late_intent = {
            "event": "intent",
            "key": "models/late.stl",
            "size": 1,
            "sha256": "c" * 64,
            "namespace": "local",
            "generation": 1,
            **binding,
        }
        path = tmp_path / ".restore-state-machine.journal"
        path.write_text(
            "\n".join(json.dumps(item) for item in (started, swap, late_intent))
        )
        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_rejects_database_active_without_swap_intent(self, tmp_path: Path) -> None:
        binding = {
            "backup_id": "active-without-swap",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        path = tmp_path / ".restore-active-without-swap.journal"
        path.write_text(
            json.dumps({"event": "started", "version": 2, **binding})
            + "\n"
            + json.dumps({"event": "database_active", **binding})
        )
        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_rejects_duplicate_blob_intent(self, tmp_path: Path) -> None:
        binding = {
            "backup_id": "duplicate-intent",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        intent = {
            "event": "intent",
            "key": "files/model.stl",
            "size": 1,
            "sha256": "c" * 64,
            "namespace": "local",
            "generation": 1,
            **binding,
        }
        path = tmp_path / ".restore-duplicate-intent.journal"
        path.write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {"event": "started", "version": 2, **binding},
                    intent,
                    intent,
                )
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_rejects_database_swap_before_all_blobs_are_published(
        self, tmp_path: Path
    ) -> None:
        binding = {
            "backup_id": "swap-before-blobs",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        intent = {
            "event": "intent",
            "key": "files/model.stl",
            "size": 1,
            "sha256": "c" * 64,
            "namespace": "local",
            "generation": 1,
            **binding,
        }
        swap = {"event": "database_swap_intent", **binding}
        path = tmp_path / ".restore-swap-before-blobs.journal"
        path.write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {"event": "started", "version": 2, **binding},
                    intent,
                    swap,
                )
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_rejects_blob_transition_after_database_active(
        self, tmp_path: Path
    ) -> None:
        binding = {
            "backup_id": "blob-after-active",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        blob = {
            "key": "files/model.stl",
            "size": 1,
            "sha256": "c" * 64,
            "namespace": "local",
            "generation": 1,
        }
        path = tmp_path / ".restore-blob-after-active.journal"
        path.write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {"event": "started", "version": 2, **binding},
                    {"event": "intent", **blob, **binding},
                    {"event": "published", **blob, **binding},
                    {"event": "database_swap_intent", **binding},
                    {"event": "database_active", **binding},
                    {
                        "event": "intent",
                        "key": "files/late.stl",
                        "size": 1,
                        "sha256": "d" * 64,
                        "namespace": "local",
                        "generation": 1,
                        **binding,
                    },
                )
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_rejects_database_swap_until_every_blob_is_published(
        self, tmp_path: Path
    ) -> None:
        binding = {
            "backup_id": "all-blobs-published",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        first = {
            "key": "files/first.stl",
            "size": 1,
            "sha256": "c" * 64,
            "namespace": "local",
            "generation": 1,
        }
        second = {
            "key": "files/second.stl",
            "size": 1,
            "sha256": "d" * 64,
            "namespace": "local",
            "generation": 1,
        }
        path = tmp_path / ".restore-all-blobs-published.journal"
        path.write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {"event": "started", "version": 2, **binding},
                    {"event": "intent", **first, **binding},
                    {"event": "published", **first, **binding},
                    {"event": "intent", **second, **binding},
                    {"event": "database_swap_intent", **binding},
                )
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)


class TestUnsafeMemberName:
    def test_verify_backup_flags_unsafe_member_name(
        self, backup_env: BackupEnv
    ) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        contents["../escape.txt"] = b"evil"
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)


class TestVerifyBackup:
    def test_verify_backup_flags_symlink_member(self, backup_env: BackupEnv) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        _write(archive, contents, extra_symlink="sneaky-link")

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)

    def test_verify_backup_flags_missing_manifest(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        del contents["manifest.json"]
        _write(archive, contents)

        result = _verify_direct(archive, monkeypatch)

        assert result.valid is False
        assert any(
            f["code"] == "backup_manifest_invalid" and f["member"] == "manifest.json"
            for f in result.findings
        )
        assert result.app_compatible is False

    def test_verify_backup_flags_corrupt_manifest_json(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        contents["manifest.json"] = b"{not valid json"
        _write(archive, contents)

        result = _verify_direct(archive, monkeypatch)

        assert result.valid is False
        assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)

    def test_verify_backup_flags_non_dict_manifest(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        contents["manifest.json"] = json.dumps(["not", "a", "dict"]).encode("utf-8")
        _write(archive, contents)

        result = _verify_direct(archive, monkeypatch)

        assert result.valid is False
        assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)

    def test_verify_backup_flags_missing_db_file(self, backup_env: BackupEnv) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        del contents["db.sqlite3"]
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(
            f["code"] == "backup_member_missing" and f["member"] == "db.sqlite3"
            for f in result.findings
        )

    def test_verify_backup_flags_files_entry_not_a_list(
        self, backup_env: BackupEnv
    ) -> None:
        archive, contents, manifest = _fresh_archive(backup_env)
        manifest["files"] = "not-a-list"
        contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(
            f["code"] == "backup_manifest_invalid" and f["member"] == "files"
            for f in result.findings
        )

    def test_verify_backup_flags_malformed_file_entry(
        self, backup_env: BackupEnv
    ) -> None:
        archive, contents, manifest = _fresh_archive(backup_env)
        manifest["files"] = [{"no_arc_key": True}]
        contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(
            f["code"] == "backup_manifest_invalid" and f["member"] == "files"
            for f in result.findings
        )

    def test_verify_backup_flags_file_entry_missing_from_archive(
        self,
        backup_env: BackupEnv,
    ) -> None:
        archive, contents, manifest = _fresh_archive(backup_env)
        manifest["files"].append({"arc": "files/ghost.stl", "size": 5})
        contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(
            f["code"] == "backup_member_missing" and f["member"] == "files/ghost.stl"
            for f in result.findings
        )

    def test_verify_backup_flags_file_size_mismatch(
        self, backup_env: BackupEnv
    ) -> None:
        archive, contents, manifest = _fresh_archive(backup_env)
        entry = manifest["files"][0]
        entry["size"] = entry["size"] + 999
        contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(f["code"] == "backup_member_size_mismatch" for f in result.findings)

    def test_verify_backup_flags_incompatible_manifest_version(
        self,
        backup_env: BackupEnv,
    ) -> None:
        archive, contents, manifest = _fresh_archive(backup_env)
        manifest["version"] = "999"
        contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.app_compatible is False
        assert any(
            f["code"] == "backup_manifest_invalid" and f["member"] == "version"
            for f in result.findings
        )

    def test_verify_backup_flags_unreadable_archive(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive, _contents, _manifest = _fresh_archive(backup_env)
        archive.write_bytes(b"not a gzip file at all")

        result = _verify_direct(archive, monkeypatch)

        assert result.valid is False
        assert any(
            f["code"] == "backup_manifest_invalid" and f["member"] == "archive"
            for f in result.findings
        )


class TestRestoreJournalV2:
    @pytest.mark.parametrize("event_name", ["database_swap_intent", "database_active"])
    def test_rejects_pre_upgrade_database_marker_event(
        self, tmp_path: Path, event_name: str
    ) -> None:
        path = tmp_path / ".restore-legacy-marker.journal"
        path.write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {
                        "event": "started",
                        "version": 1,
                        "backup_id": "legacy-marker",
                        "archive_sha256": "a" * 64,
                        "backend": "local",
                        "namespaces": [],
                    },
                    {"event": event_name, "backup_id": "legacy-marker"},
                )
            )
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_reports_no_recovery_when_journal_directory_is_unreadable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_glob(_path: Path, _pattern: str):
            raise OSError("directory unavailable")

        monkeypatch.setattr(Path, "glob", fail_glob)

        try:
            assert backup.inspect_restore_recovery() is True
            assert backup.restore_in_progress() is True
        finally:
            backup._restore_gate.clear()

    def test_gates_recovery_when_database_swap_is_journaled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started = {
            "event": "started",
            "version": 2,
            "backup_id": "swap",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
            "backend": "local",
            "namespaces": [],
        }
        swap = {
            "event": "database_swap_intent",
            "backup_id": "swap",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        (tmp_path / ".restore-swap.journal").write_text(
            json.dumps(started) + "\n" + json.dumps(swap) + "\n"
        )
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)
        marker_calls: list[tuple[object, ...]] = []
        monkeypatch.setattr(
            backup,
            "_active_restore_marker",
            lambda *args, **kwargs: marker_calls.append((args, kwargs)) or True,
        )
        backup._restore_gate.clear()

        assert backup.inspect_restore_recovery() is True
        assert backup.restore_in_progress() is True
        assert marker_calls == [
            (
                ("swap",),
                {
                    "operation_nonce": "b" * 64,
                    "archive_sha256": "a" * 64,
                },
            )
        ]
        backup._restore_gate.clear()

    def test_legacy_swap_without_binding_stays_gated_without_marker_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        journal = tmp_path / ".restore-legacy-swap.journal"
        journal.touch()
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)
        monkeypatch.setattr(
            backup,
            "_load_restore_journal",
            lambda _path: SimpleNamespace(
                started={"backup_id": "legacy-swap"},
                database_swap_intent=True,
                database_active=False,
            ),
        )

        def fail_marker_lookup(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("an unbound legacy journal cannot query a marker")

        monkeypatch.setattr(backup, "_active_restore_marker", fail_marker_lookup)
        backup._restore_gate.clear()
        try:
            assert backup.inspect_restore_recovery() is True
            assert backup.restore_in_progress() is True
        finally:
            backup._restore_gate.clear()

    def test_returns_none_when_journal_discovery_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_glob(_path: Path, _pattern: str):
            raise OSError("directory unavailable")

        monkeypatch.setattr(Path, "glob", fail_glob)

        assert backup.unresolved_restore_backup_id() is None

    def test_returns_none_when_multiple_journals_are_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".restore-one.journal").write_text("{}")
        (tmp_path / ".restore-two.journal").write_text("{}")
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)

        assert backup.unresolved_restore_backup_id() is None

    def test_returns_none_for_a_journal_with_an_invalid_filename(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "glob", lambda *_args: [Path("invalid")])

        assert backup.unresolved_restore_backup_id() is None

    def test_returns_none_for_a_journal_without_a_backup_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "glob", lambda *_args: [Path(".restore-.journal")])

        assert backup.unresolved_restore_backup_id() is None

    def test_routes_a_readable_journal_to_its_filename_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        journal = tmp_path / ".restore-routed.journal"
        journal.write_text('{"event":"started","backup_id":"tampered"}\n')
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)

        assert backup.unresolved_restore_backup_id() == "routed"

    def test_rejects_a_non_object_journal_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".restore-list.journal").write_text("[]\n")
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)

        assert backup.unresolved_restore_backup_id() is None

    def test_rejects_an_empty_journal_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".restore-empty.journal").write_bytes(b"")
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)

        assert backup.unresolved_restore_backup_id() is None

    def test_upgrades_a_matching_v1_journal_forward_only(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-abc.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 1,
                    "backup_id": "abc",
                    "archive_sha256": "a" * 64,
                    "backend": "local",
                    "namespaces": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        state = backup._prepare_restore_journal(  # noqa: SLF001
            path,
            backup_id="abc",
            archive_sha256="a" * 64,
            blobs=[],
        )

        assert state.started["version"] == 2
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert events[-1]["event"] == "journal_upgrade"
        assert events[-1]["backup_id"] == "abc"
        assert events[-1]["from_version"] == 1
        assert events[-1]["to_version"] == 2
        assert isinstance(events[-1]["operation_nonce"], str)
        assert len(events[-1]["operation_nonce"]) == 64
        assert events[-1]["archive_sha256"] == "a" * 64

    def test_rejects_a_duplicate_v1_journal_upgrade(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-duplicate-upgrade.journal"
        started = {
            "event": "started",
            "version": 1,
            "backup_id": "duplicate-upgrade",
            "archive_sha256": "a" * 64,
            "backend": "local",
            "namespaces": [],
        }
        upgrade = {
            "event": "journal_upgrade",
            "backup_id": "duplicate-upgrade",
            "from_version": 1,
            "to_version": 2,
            "operation_nonce": "b" * 64,
            "archive_sha256": "a" * 64,
        }
        path.write_text(
            "\n".join(json.dumps(event) for event in (started, upgrade, upgrade))
        )
        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_projects_v1_upgrade_identity_into_marker_proof(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / ".restore-projected.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 1,
                    "backup_id": "projected",
                    "archive_sha256": "a" * 64,
                    "backend": "local",
                    "namespaces": [],
                }
            )
            + "\n"
            + json.dumps(
                {
                    "event": "journal_upgrade",
                    "backup_id": "projected",
                    "from_version": 1,
                    "to_version": 2,
                    "operation_nonce": "b" * 64,
                    "archive_sha256": "a" * 64,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        state = backup._load_restore_journal(path)
        assert state.started["version"] == 2
        assert state.started["operation_nonce"] == "b" * 64

    def test_upgrades_v1_after_existing_blob_lifecycle(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-published.journal"
        started = {
            "event": "started",
            "version": 1,
            "backup_id": "published",
            "archive_sha256": "a" * 64,
            "backend": "local",
            "namespaces": [],
        }
        intent = {
            "event": "intent",
            "key": "models/blob.stl",
            "size": 3,
            "sha256": "c" * 64,
            "namespace": "local",
            "generation": 1,
        }
        published = {
            "event": "published",
            "key": "models/blob.stl",
            "generation": 1,
            "namespace": "local",
        }
        retracted = {
            "event": "retracted",
            "key": "models/blob.stl",
            "generation": 1,
        }
        upgrade = {
            "event": "journal_upgrade",
            "backup_id": "published",
            "from_version": 1,
            "to_version": 2,
            "operation_nonce": "b" * 64,
            "archive_sha256": "a" * 64,
        }
        path.write_text(
            "\n".join(
                json.dumps(event)
                for event in (started, intent, published, retracted, upgrade)
            )
            + "\n",
            encoding="utf-8",
        )
        state = backup._load_restore_journal(path)
        assert state.started["version"] == 2
        assert state.intents == {}
        assert state.published == {}

    def test_prepare_v1_journal_appends_upgrade_after_existing_lifecycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / ".restore-prepare-v1.journal"
        key = "files/blob.stl"
        archive_hash = "a" * 64
        path.write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {
                        "event": "started",
                        "version": 1,
                        "backup_id": "prepare-v1",
                        "archive_sha256": archive_hash,
                        "backend": "local",
                        "namespaces": ["local"],
                    },
                    {
                        "event": "intent",
                        "key": key,
                        "size": 3,
                        "sha256": "c" * 64,
                        "namespace": "local",
                        "generation": 1,
                    },
                )
            )
            + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            backup,
            "get_backend",
            lambda: SimpleNamespace(backend_name="local"),
        )
        blob = backup._StagedBlob(
            key=key,
            path=tmp_path / "blob.stl",
            size=3,
            sha256="c" * 64,
            namespace="local",
        )

        state = backup._prepare_restore_journal(
            path,
            backup_id="prepare-v1",
            archive_sha256=archive_hash,
            blobs=[blob],
            operation_nonce="b" * 64,
        )

        assert state.started["version"] == 2
        assert state.started["operation_nonce"] == "b" * 64
        assert set(state.intents) == {key}
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert [event["event"] for event in events] == [
            "started",
            "intent",
            "journal_upgrade",
        ]

    @pytest.mark.parametrize("lifecycle", ["intent", "published"])
    def test_v1_upgrade_preserves_active_blob_lifecycle(
        self, tmp_path: Path, lifecycle: str
    ) -> None:
        path = tmp_path / f".restore-active-{lifecycle}.journal"
        started = {
            "event": "started",
            "version": 1,
            "backup_id": "active",
            "archive_sha256": "a" * 64,
            "backend": "local",
            "namespaces": [],
        }
        intent = {
            "event": "intent",
            "key": "models/blob.stl",
            "size": 3,
            "sha256": "c" * 64,
            "namespace": "local",
            "generation": 1,
        }
        published = {
            "event": "published",
            "key": "models/blob.stl",
            "generation": 1,
            "namespace": "local",
        }
        upgrade = {
            "event": "journal_upgrade",
            "backup_id": "active",
            "from_version": 1,
            "to_version": 2,
            "operation_nonce": "b" * 64,
            "archive_sha256": "a" * 64,
        }
        events: list[dict[str, object]] = [started, intent]
        if lifecycle == "published":
            events.append(published)
        events.append(upgrade)
        path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

        state = backup._load_restore_journal(path)

        assert set(state.intents) == {"models/blob.stl"}
        assert (
            set(state.published) == {"models/blob.stl"}
            if lifecycle == "published"
            else state.published == {}
        )

    def test_interrupted_journal_gates_mutations_across_restart(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / ".restore-resume.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 2,
                    "backup_id": "resume",
                    "archive_sha256": "a" * 64,
                    "operation_nonce": "b" * 64,
                    "backend": "local",
                    "namespaces": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _overlay["backup_dir"] = tmp_path
        backup._restore_gate.clear()
        try:
            assert backup.inspect_restore_recovery() is True
            assert backup.restore_in_progress() is True
            assert backup.unresolved_restore_backup_id() == "resume"
        finally:
            backup._restore_gate.clear()
            _overlay.pop("backup_dir", None)

    def test_invalid_journal_allows_no_restore_bypass(self, tmp_path: Path) -> None:
        (tmp_path / ".restore-unknown.journal").write_text("not-json\n")
        _overlay["backup_dir"] = tmp_path
        backup._restore_gate.clear()
        try:
            assert backup.inspect_restore_recovery() is True
            # A malformed journal has no resumable identity.  The maintenance
            # gate remains set, so no other backup can bypass the unresolved
            # operation.
            assert backup.unresolved_restore_backup_id() is None
        finally:
            backup._restore_gate.clear()
            _overlay.pop("backup_dir", None)

    def test_no_journal_leaves_restore_maintenance_clear(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)
        backup._restore_gate.clear()

        assert backup.inspect_restore_recovery() is False
        assert backup.restore_in_progress() is False

    def test_unreadable_journal_directory_keeps_pending_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_glob(_path: Path, _pattern: str):
            raise OSError("directory unavailable")

        monkeypatch.setattr(Path, "glob", fail_glob)

        assert backup._restore_journal_pending() is True

    def test_rejects_an_unbalanced_mutating_operation(self) -> None:
        backup._active_mutations = 0

        with pytest.raises(RuntimeError, match="unbalanced_mutating_operation"):
            backup.end_mutating_operation()

    def test_drains_a_balanced_mutating_operation(self) -> None:
        backup._restore_gate.clear()
        assert backup.begin_mutating_operation() is True
        backup.end_mutating_operation()

    def test_times_out_when_a_mutation_never_drains(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup._restore_gate.clear()
        backup._active_mutations = 1
        monkeypatch.setattr(backup, "_RESTORE_DRAIN_TIMEOUT_S", 0)
        try:
            with pytest.raises(backup.RestoreConflictError, match="still active"):
                backup._begin_restore_maintenance()
            assert backup.restore_in_progress() is False
        finally:
            backup._active_mutations = 0

    def test_rejects_mutation_while_restore_maintenance_is_active(self) -> None:
        backup._restore_gate.set()
        try:
            assert backup.begin_mutating_operation() is False
        finally:
            backup._restore_gate.clear()


class TestBackupStorageHelpers:
    def test_backup_s3_target_repr_does_not_expose_credentials(self) -> None:
        target = backup._BackupS3Target(
            client=object(),
            bucket="backup-bucket",
            signature="fingerprint",
        )

        rendered = repr(target)

        assert "access-key" not in rendered
        assert "secret-key" not in rendered

    def test_s3_archive_download_requires_an_immutable_identity(self) -> None:
        target = backup._BackupS3Target(
            client=object(),
            bucket="backup-bucket",
            signature="fingerprint",
        )

        with pytest.raises(
            backup.BackupOwnershipError,
            match="backup_remote_identity_unavailable",
        ):
            backup._download_s3_archive(target, "printstash-backups/archive.tar.gz")

    def test_s3_archive_download_pins_etag_then_closes_the_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, str]] = []
        body = io.BytesIO(b"remote-archive")

        class Store:
            def get_object(self, **kwargs: str) -> dict[str, object]:
                calls.append(kwargs)
                return {"Body": body, "ETag": '"etag"'}

        target = backup._BackupS3Target(
            client=Store(),
            bucket="backup-bucket",
            signature="fingerprint",
        )
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)

        archive, response = backup._download_s3_archive(
            target,
            "printstash-backups/archive.tar.gz",
            etag='"etag"',
        )

        assert archive.read_bytes() == b"remote-archive"
        assert response["ETag"] == '"etag"'
        assert body.closed is True
        assert calls == [
            {
                "Bucket": "backup-bucket",
                "Key": "printstash-backups/archive.tar.gz",
                "IfMatch": '"etag"',
            }
        ]

    def test_remote_identity_is_required_before_candidate_validation(self) -> None:
        with pytest.raises(RuntimeError, match="backup_remote_identity_unavailable"):
            backup._require_remote_identity({})

    def test_unconfigured_remote_provider_has_no_adoption_candidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "_get_backup_s3_target", lambda: None)

        assert backup.discover_unowned_s3_backups() == []

    def test_missing_local_backup_root_has_no_adoption_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path / "missing")

        assert backup.discover_unowned_local_backups() == []

    def test_remote_download_refuses_an_unavailable_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        meta = backup.BackupMeta(
            id="remote",
            created_at="2026-01-01T00:00:00+00:00",
            size_bytes=1,
            storage_backend="s3",
            file_count=0,
            app_version="0.13.0",
            path="printstash-backups/remote.tar.gz",
            location="s3",
        )
        monkeypatch.setattr(backup, "_get_backup_s3_target", lambda: None)

        with pytest.raises(RuntimeError, match="no S3 client"):
            backup._download_backup_to_local(meta)

    def test_unknown_source_reference_never_falls_back_to_a_sibling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        meta = backup.BackupMeta(
            id="shared",
            created_at="2026-01-01T00:00:00+00:00",
            size_bytes=1,
            storage_backend="local",
            file_count=0,
            app_version="0.13.0",
            path="/backups/shared.tar.gz",
            location="local",
            source_ref="known-source",
        )
        monkeypatch.setattr(backup, "list_backup_sources", lambda: [meta])

        assert backup.get_backup("shared", source_ref="unknown-source") is None

    def test_rejects_a_non_sqlite_database_backup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "db_url", "postgresql://db.example/vault")

        with pytest.raises(
            backup.DatabaseBackupNotSupportedError,
            match="database_backup_not_supported",
        ):
            backup._require_database_backup_support()

    def test_returns_none_when_cloud_backups_are_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "_backup_s3", None)
        monkeypatch.setitem(_overlay, "backup_s3_bucket", None)

        assert backup._get_backup_s3() is None

    def test_cache_refreshes_after_backup_bucket_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clients: list[object] = []

        class Client:
            pass

        monkeypatch.setitem(_overlay, "backup_s3_bucket", "first-bucket")
        monkeypatch.setattr(
            "boto3.client", lambda **_kwargs: clients.append(Client()) or clients[-1]
        )
        monkeypatch.setattr(backup, "_backup_s3", None)
        monkeypatch.setattr(backup, "_backup_s3_target", None)
        first = backup._get_backup_s3_target()
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "second-bucket")
        second = backup._get_backup_s3_target()

        assert first is not None and second is not None
        assert first.bucket == "first-bucket"
        assert second.bucket == "second-bucket"
        assert first.client is not second.client

    @pytest.mark.parametrize(
        ("field", "first", "second"),
        [
            ("backup_s3_endpoint_url", "https://one.example", "https://two.example"),
            ("backup_s3_region", "region-one", "region-two"),
            ("backup_s3_access_key", "access-one", "access-two"),
            ("backup_s3_secret_key", "secret-one", "secret-two"),
        ],
    )
    def test_cache_refreshes_after_every_target_component_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        field: str,
        first: str,
        second: str,
    ) -> None:
        calls: list[dict[str, object]] = []

        def client(**kwargs: object) -> object:
            calls.append(kwargs)
            return object()

        monkeypatch.setattr("boto3.client", client)
        monkeypatch.setattr(backup, "_backup_s3", None)
        monkeypatch.setattr(backup, "_backup_s3_target", None)
        monkeypatch.setattr(backup, "_backup_s3_last_signature", None)
        baseline = {
            "backup_s3_bucket": "target-bucket",
            "backup_s3_endpoint_url": "https://one.example",
            "backup_s3_region": "region-one",
            "backup_s3_access_key": "access-one",
            "backup_s3_secret_key": "secret-one",
        }
        for name, value in baseline.items():
            monkeypatch.setitem(_overlay, name, value)

        first_target = backup._get_backup_s3_target()
        monkeypatch.setitem(_overlay, field, second)
        second_target = backup._get_backup_s3_target()

        assert first_target is not None and second_target is not None
        assert first_target.client is not second_target.client
        assert len(calls) == 2
        boto_field = {
            "backup_s3_endpoint_url": "endpoint_url",
            "backup_s3_region": "region_name",
            "backup_s3_access_key": "aws_access_key_id",
            "backup_s3_secret_key": "aws_secret_access_key",
        }[field]
        assert calls[0][boto_field] == first
        assert calls[1][boto_field] == second

    def test_cache_refreshes_after_credentials_are_cleared(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, object]] = []
        monkeypatch.setattr(
            "boto3.client", lambda **kwargs: calls.append(kwargs) or object()
        )
        monkeypatch.setattr(backup, "_backup_s3", None)
        monkeypatch.setattr(backup, "_backup_s3_target", None)
        monkeypatch.setattr(backup, "_backup_s3_last_signature", None)
        for name, value in {
            "backup_s3_bucket": "target-bucket",
            "backup_s3_access_key": "access-one",
            "backup_s3_secret_key": "secret-one",
        }.items():
            monkeypatch.setitem(_overlay, name, value)

        first = backup._get_backup_s3_target()
        monkeypatch.setitem(_overlay, "backup_s3_access_key", "")
        monkeypatch.setitem(_overlay, "backup_s3_secret_key", "")
        second = backup._get_backup_s3_target()

        assert first is not None and second is not None
        assert first.client is not second.client
        assert calls[0]["aws_access_key_id"] == "access-one"
        assert calls[0]["aws_secret_access_key"] == "secret-one"
        assert calls[1]["aws_access_key_id"] is None
        assert calls[1]["aws_secret_access_key"] is None

    def test_retries_client_construction_after_transient_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts = 0

        def client(**_kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("temporarily unavailable")
            return object()

        monkeypatch.setattr("boto3.client", client)
        monkeypatch.setattr(backup, "_backup_s3", None)
        monkeypatch.setattr(backup, "_backup_s3_target", None)
        monkeypatch.setattr(backup, "_backup_s3_last_signature", None)
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "target-bucket")

        assert backup._get_backup_s3_target() is None
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "repaired-bucket")
        repaired = backup._get_backup_s3_target()

        assert repaired is not None
        assert attempts == 2

    def test_config_snapshot_is_never_mixed_during_atomic_runtime_updates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = {
            "backup_s3_bucket": "bucket-a",
            "backup_s3_endpoint_url": "https://a.example",
            "backup_s3_region": "region-a",
            "backup_s3_access_key": "access-a",
            "backup_s3_secret_key": "secret-a",
        }
        new = {
            "backup_s3_bucket": "bucket-b",
            "backup_s3_endpoint_url": "https://b.example",
            "backup_s3_region": "region-b",
            "backup_s3_access_key": "access-b",
            "backup_s3_secret_key": "secret-b",
        }
        for name, value in old.items():
            monkeypatch.setitem(_overlay, name, value)
        snapshots: list[tuple[str, str, str, str, str]] = []
        stop = threading.Event()

        def writer() -> None:
            while not stop.is_set():
                _overlay.update(new)
                _overlay.update(old)

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            snapshots.extend(backup._backup_s3_config() for _ in range(2_000))
        finally:
            stop.set()
            thread.join(timeout=2)

        assert snapshots
        assert set(snapshots) <= {
            (
                old["backup_s3_bucket"],
                old["backup_s3_endpoint_url"],
                old["backup_s3_region"],
                old["backup_s3_access_key"],
                old["backup_s3_secret_key"],
            ),
            (
                new["backup_s3_bucket"],
                new["backup_s3_endpoint_url"],
                new["backup_s3_region"],
                new["backup_s3_access_key"],
                new["backup_s3_secret_key"],
            ),
        }

    def test_captured_s3_operation_keeps_original_target_after_config_rotation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, object]] = []

        class Store:
            def head_object(self, **kwargs: object) -> dict[str, object]:
                calls.append(kwargs)
                return {"ContentLength": 1, "ETag": '"etag"'}

        target = backup._BackupS3Target(
            client=Store(),
            bucket="bucket-a",
            signature="a",
        )
        row = backup.OwnedStorageObject(
            backend="backup-s3",
            namespace="bucket-a/printstash-backups/",
            key="printstash-backups/archive.tar.gz",
            object_kind="backup",
            state=backup.StorageObjectState.COMMITTED,
            size_bytes=1,
            etag='"etag"',
        )
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "bucket-b")

        backup._s3_head_owned(target, row)

        assert calls == [{"Bucket": "bucket-a", "Key": row.key, "IfMatch": '"etag"'}]

    @pytest.mark.parametrize(
        ("version_id", "etag", "expected"),
        [
            ("version-7", '"etag-7"', {"VersionId": "version-7"}),
            (None, '"etag-7"', {"IfMatch": '"etag-7"'}),
        ],
    )
    def test_remote_operations_use_exact_version_or_conditional_etag(
        self,
        version_id: str | None,
        etag: str | None,
        expected: dict[str, str],
    ) -> None:
        row = backup.OwnedStorageObject(
            backend="backup-s3",
            namespace="bucket-a/printstash-backups/",
            key="printstash-backups/archive.tar.gz",
            object_kind="backup",
            state=backup.StorageObjectState.COMMITTED,
            version_id=version_id,
            etag=etag,
        )

        assert backup._s3_object_kwargs(bucket="bucket-a", key=row.key, row=row) == {
            "Bucket": "bucket-a",
            "Key": row.key,
            **expected,
        }

    def test_remote_operation_without_stable_identity_fails_closed(self) -> None:
        row = backup.OwnedStorageObject(
            backend="backup-s3",
            namespace="bucket-a/printstash-backups/",
            key="printstash-backups/archive.tar.gz",
            object_kind="backup",
            state=backup.StorageObjectState.COMMITTED,
        )

        with pytest.raises(
            backup.BackupOwnershipError, match="remote_identity_unavailable"
        ):
            backup._s3_object_kwargs(bucket="bucket-a", key=row.key, row=row)


class TestListS3BackupsSafety:
    class _Store:
        def __init__(self, key: str, head: dict[str, object] | None = None) -> None:
            self.key = key
            self.head = head or {}
            self.head_calls = 0

        def get_paginator(self, _name: str) -> "TestListS3BackupsSafety._Store":
            return self

        def paginate(self, **kwargs: object) -> list[dict[str, object]]:
            prefix = str(kwargs["Prefix"])
            return [
                {"Contents": [{"Key": self.key, "Size": 1}]}
                if prefix == self.key.split("archive", 1)[0]
                else {"Contents": []}
            ]

        def head_object(self, **_kwargs: object) -> dict[str, object]:
            self.head_calls += 1
            return {"ContentLength": 1, "ETag": '"etag"', **self.head}

    def test_tokenless_unowned_object_is_not_in_normal_listing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key = "nexus3d-backups/archive-legacy.tar.gz"
        store = self._Store(key)
        target = backup._BackupS3Target(
            client=store,
            bucket="archive-bucket",
            signature="target",
        )
        monkeypatch.setattr(backup, "_get_backup_s3_target", lambda: target)
        monkeypatch.setattr(backup, "_backup_ownership_rows", lambda **_kwargs: [])

        assert backup._list_s3_backups() == []
        assert store.head_calls == 0

    def test_owned_object_without_etag_or_version_is_not_listable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key = "printstash-backups/archive-legacy.tar.gz"
        store = self._Store(key)
        target = backup._BackupS3Target(
            client=store,
            bucket="archive-bucket",
            signature="target",
        )
        row = backup.OwnedStorageObject(
            backend="backup-s3",
            namespace="archive-bucket/printstash-backups/",
            key=key,
            object_kind="backup-legacy",
            state=backup.StorageObjectState.COMMITTED,
            size_bytes=1,
            sha256="a" * 64,
        )
        monkeypatch.setattr(backup, "_get_backup_s3_target", lambda: target)
        monkeypatch.setattr(backup, "_backup_ownership_rows", lambda **_kwargs: [row])

        assert backup._list_s3_backups() == []
        assert store.head_calls == 0

    @pytest.mark.parametrize(
        "head_overrides",
        [
            {"ContentLength": 2},
            {"ETag": '"replacement"'},
            {"Metadata": {"printstash-create-token": "wrong-token"}},
        ],
    )
    def test_owned_object_with_mismatched_head_proof_is_not_listable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        head_overrides: dict[str, object],
    ) -> None:
        key = "printstash-backups/archive-legacy.tar.gz"
        store = self._Store(key, head_overrides)
        target = backup._BackupS3Target(
            client=store,
            bucket="archive-bucket",
            signature="target",
        )
        row = backup.OwnedStorageObject(
            backend="backup-s3",
            namespace="archive-bucket/printstash-backups/",
            key=key,
            object_kind="backup",
            state=backup.StorageObjectState.COMMITTED,
            token="expected-token",
            size_bytes=1,
            sha256="a" * 64,
            etag='"etag"',
        )
        monkeypatch.setattr(backup, "_get_backup_s3_target", lambda: target)
        monkeypatch.setattr(backup, "_backup_ownership_rows", lambda **_kwargs: [row])

        assert backup._list_s3_backups() == []


class TestListBackupsSafety:
    def test_unknown_hash_collision_is_not_deduplicated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        local = backup.BackupMeta(
            id="same-id",
            created_at="2020-01-01T00:00:00+00:00",
            size_bytes=1,
            storage_backend="local",
            file_count=0,
            app_version="0.13.0",
            path="/vault/same.tar.gz",
            archive_sha256="a" * 64,
            source_ref="local-ref",
        )
        remote = backup.BackupMeta(
            id="same-id",
            created_at=local.created_at,
            size_bytes=1,
            storage_backend="s3",
            file_count=0,
            app_version="0.13.0",
            path="nexus3d-backups/same.tar.gz",
            location="s3",
            source_ref="remote-ref",
        )
        monkeypatch.setattr(backup, "reconcile_backup_publications", lambda: 0)
        monkeypatch.setattr(backup, "_list_local_backups", lambda: [local])
        monkeypatch.setattr(backup, "_list_s3_backups", lambda: [remote])

        assert {item.source_ref for item in backup.list_backups()} == {
            "local-ref",
            "remote-ref",
        }

    def test_collision_requires_source_ref_when_archive_hashes_differ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        left = backup.BackupMeta(
            id="same-id",
            created_at="2020-01-01T00:00:00+00:00",
            size_bytes=1,
            storage_backend="local",
            file_count=0,
            app_version="0.13.0",
            path="/vault/left.tar.gz",
            archive_sha256="a" * 64,
            source_ref="left-ref",
        )
        right = backup.BackupMeta(
            id="same-id",
            created_at="2020-01-01T00:00:00+00:00",
            size_bytes=2,
            storage_backend="s3",
            file_count=0,
            app_version="0.13.0",
            path="nexus3d-backups/right.tar.gz",
            location="s3",
            archive_sha256="b" * 64,
            source_ref="right-ref",
        )
        monkeypatch.setattr(backup, "reconcile_backup_publications", lambda: 0)
        monkeypatch.setattr(backup, "_list_local_backups", lambda: [left])
        monkeypatch.setattr(backup, "_list_s3_backups", lambda: [right])

        assert {meta.source_ref for meta in backup.list_backups()} == {
            "left-ref",
            "right-ref",
        }
        with pytest.raises(
            backup.BackupIdentityConflictError, match="identity_conflict"
        ):
            backup.get_backup("same-id")
        assert backup.get_backup("same-id", source_ref="right-ref") == right

    def test_returns_none_when_cloud_client_initialization_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "_backup_s3", None)
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "backup-bucket")
        monkeypatch.setattr(
            "boto3.client", lambda **_kwargs: (_ for _ in ()).throw(OSError("offline"))
        )

        assert backup._get_backup_s3() is None
        assert backup._backup_s3 is False

    def test_rejects_a_missing_sqlite_database_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "_db_path", lambda: tmp_path / "missing.sqlite")

        with pytest.raises(FileNotFoundError):
            with backup._sqlite_snapshot_file():
                pass

    def test_rejects_a_snapshot_with_a_failed_integrity_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Connection:
            def __enter__(self) -> "_Connection":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def execute(self, _statement: str) -> "_Connection":
                return self

            def fetchone(self) -> tuple[str]:
                return ("corrupt",)

        monkeypatch.setattr(backup.sqlite3, "connect", lambda _path: _Connection())

        with pytest.raises(RuntimeError, match="integrity_check_failed"):
            backup._validate_sqlite_snapshot(tmp_path / "snapshot.sqlite")

    def test_restores_database_bytes_through_a_temporary_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)
        target = tmp_path / "vault.sqlite"
        observed: list[Path] = []

        monkeypatch.setattr(backup, "_db_path", lambda: target)

        def capture_snapshot(path: Path) -> None:
            observed.append(path)
            assert path.read_bytes() == b"snapshot-bytes"

        monkeypatch.setattr(backup, "_restore_database_from_path", capture_snapshot)

        backup._restore_database(b"snapshot-bytes")

        assert len(observed) == 1
        assert not observed[0].exists()

    def test_uses_engine_fallback_when_factory_has_no_dispose(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Engine:
            disposed = False

            def dispose(self) -> None:
                self.disposed = True

        engine = _Engine()
        monkeypatch.setattr(backup, "get_session_factory", lambda: object())
        monkeypatch.setattr(backup, "get_engine", lambda: engine)

        backup._dispose_session_engine()

        assert engine.disposed is True

    def test_returns_unknown_when_active_marker_read_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_factory():
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(backup, "get_session_factory", fail_factory)

        assert (
            backup._active_restore_marker(
                "backup-id", operation_nonce="b" * 64, archive_sha256="a" * 64
            )
            is None
        )

    def test_returns_false_when_active_marker_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Result:
            def first(self) -> None:
                return None

        class _Session:
            def __enter__(self) -> "_Session":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def exec(self, _statement: object) -> _Result:
                return _Result()

        class _Factory:
            def session(self) -> _Session:
                return _Session()

        monkeypatch.setattr(backup, "get_session_factory", lambda: _Factory())

        assert (
            backup._active_restore_marker(
                "backup-id", operation_nonce="b" * 64, archive_sha256="a" * 64
            )
            is False
        )

    def test_rejects_a_journal_with_an_unknown_version(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-unknown.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 99,
                    "backup_id": "unknown",
                    "archive_sha256": "a" * 64,
                    "operation_nonce": "b" * 64,
                }
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_rejects_a_v2_journal_with_an_invalid_nonce(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-invalid-nonce.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 2,
                    "backup_id": "invalid-nonce",
                    "archive_sha256": "a" * 64,
                    "operation_nonce": "z" * 64,
                }
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_rejects_a_duplicate_database_swap_event(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-duplicate-swap.journal"
        started = {
            "event": "started",
            "version": 2,
            "backup_id": "duplicate-swap",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        swap = {
            "event": "database_swap_intent",
            "backup_id": "duplicate-swap",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        path.write_text("\n".join(json.dumps(event) for event in (started, swap, swap)))

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_accepts_a_terminal_complete_journal_with_identity(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / ".restore-complete.journal"
        started = {
            "event": "started",
            "version": 2,
            "backup_id": "complete",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        path.write_text(
            json.dumps(started)
            + "\n"
            + json.dumps(
                {
                    "event": "database_swap_intent",
                    "backup_id": "complete",
                    "operation_nonce": "b" * 64,
                    "archive_sha256": "a" * 64,
                }
            )
            + "\n"
            + json.dumps(
                {
                    "event": "database_active",
                    "backup_id": "complete",
                    "operation_nonce": "b" * 64,
                    "archive_sha256": "a" * 64,
                }
            )
            + "\n"
            + json.dumps(
                {
                    "event": "complete",
                    "backup_id": "complete",
                    "operation_nonce": "b" * 64,
                    "archive_sha256": "a" * 64,
                }
            )
            + "\n"
        )

        state = backup._load_restore_journal(path)
        assert state.started["backup_id"] == "complete"

    def test_rejects_events_after_terminal_complete(self, tmp_path: Path) -> None:
        binding = {
            "backup_id": "terminal-order",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        path = tmp_path / ".restore-terminal-order.journal"
        path.write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {"event": "started", "version": 2, **binding},
                    {"event": "database_swap_intent", **binding},
                    {"event": "database_active", **binding},
                    {"event": "complete", **binding},
                    {"event": "database_active", **binding},
                )
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_removes_a_completed_journal_file(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-cleanup.journal"
        path.write_text("complete\n")

        backup._remove_restore_journal(path)

        assert not path.exists()

    def test_rejects_a_journal_event_without_a_generation(self) -> None:
        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._journal_generation({})

    def test_skips_a_backup_with_an_invalid_creation_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        candidate = backup.BackupMeta(
            id="invalid-date",
            created_at="not-a-timestamp",
            size_bytes=1,
            storage_backend="local",
            file_count=0,
            app_version="0.13.0",
            path="invalid-date.tar.gz",
        )
        monkeypatch.setattr(backup, "list_backups", lambda: [candidate])

        assert backup.purge_old_backups(retain_days=1) == 0
