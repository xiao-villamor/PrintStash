"""The first-run wizard: the one unauthenticated write surface, and how it closes.

While the vault is unconfigured this router accepts a POST with no credentials at all —
it has to, because there is nobody to authenticate as yet. Three things keep that from
being a takeover: the browser preparation session, the refusal to run twice,
and the refusal to run at all once a user exists. If any of them regresses, anyone who can
reach the port can seize an established vault, so those rows are the point of this file.

The storage validation is the other half. It runs *before* the database is touched,
because a vault directory that already holds someone's model library is not an empty
blob store, and roots that overlap — directly or through a symlink — would let one
subsystem delete another's files. Every rejection asserts that no user was created, not
just that the status code was 400.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import SystemConfig, User
from app.services import runtime_config
from tests.factories import build_user

USERNAME = "admin"
PASSWORD = "Password123"


@pytest.fixture(autouse=True)
def prepared_browser(client: TestClient) -> None:
    _overlay["setup_mode"] = "trusted_network"
    _overlay["setup_allowed_hosts"] = "testserver"
    client.headers["Origin"] = "http://testserver"
    response = client.post("/api/v1/setup/session")
    assert response.status_code == 200, response.text
    client.headers["X-PrintStash-Setup-CSRF"] = response.json()["csrf"]


@pytest.fixture(autouse=True)
def runtime_dirs(tmp_path: Path) -> Path:
    """Point every managed root at the test's own tmp dir."""
    _overlay["staging_dir"] = tmp_path / "staging"
    _overlay["backup_dir"] = tmp_path / "backups"
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    return tmp_path


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "username": USERNAME,
        "password": PASSWORD,
        "storage_backend": "local",
    }
    body.update(overrides)
    return body


def _complete(client: TestClient, **overrides: Any):
    return client.post("/api/v1/setup", json=_payload(**overrides))


def _sftp_payload(**overrides: Any) -> dict[str, Any]:
    body = _payload()
    body.pop("storage_backend")
    body.update(
        storage_provider="sftp",
        storage_provider_config={
            "provider": "sftp",
            "host": "nas.example",
            "port": 22,
            "username": "printstash",
            "password": "contract-secret",
            "host_key": "[nas.example]:22 ssh-ed25519 AAAA",
            "root": "vault-data",
        },
    )
    body.update(overrides)
    return body


def _hostile_path(failing_call: str):
    """A ``Path`` stand-in whose *one* named call fails the way a bad mount does.

    ``pathlib.Path`` cannot be subclassed usefully on 3.11, and a real filesystem
    cannot be made to refuse ``mkdir`` or ``iterdir`` on demand, so this delegates
    everything except the call under test.
    """

    class _HostilePath:
        def __init__(self, *parts: Any) -> None:
            self._path = Path(*parts)

        def _wrap(self, path: Path) -> "_HostilePath":
            return _HostilePath(path)

        def resolve(self, *args: Any, **kwargs: Any) -> "_HostilePath":
            if failing_call == "resolve":
                raise OSError("cannot resolve")
            return self._wrap(self._path.resolve(*args, **kwargs))

        def expanduser(self) -> "_HostilePath":
            return self._wrap(self._path.expanduser())

        def mkdir(self, *args: Any, **kwargs: Any) -> None:
            if failing_call == "mkdir":
                raise OSError("read-only filesystem")
            self._path.mkdir(*args, **kwargs)

        def iterdir(self):
            if failing_call == "iterdir":
                raise OSError("cannot list")
            return self._path.iterdir()

        def unlink(self, *args: Any, **kwargs: Any) -> None:
            if failing_call == "unlink":
                raise OSError("cannot unlink")
            self._path.unlink(*args, **kwargs)

        def exists(self) -> bool:
            return self._path.exists()

        def is_dir(self) -> bool:
            return self._path.is_dir()

        def __truediv__(self, other: Any) -> "_HostilePath":
            return self._wrap(self._path / other)

        def __fspath__(self) -> str:
            return str(self._path)

        def __str__(self) -> str:
            return str(self._path)

    return _HostilePath


class TestSetupStatus:
    def test_reports_registration_disabled_by_default(self, client: TestClient) -> None:
        _overlay["setup_mode"] = "disabled"
        response = client.get("/api/v1/setup/status")

        assert response.json()["setup_available"] is False

    def test_reports_an_unconfigured_vault(self, client: TestClient) -> None:
        body = client.get("/api/v1/setup/status").json()

        assert body["configured"] is False
        assert body["setup_available"] is True

    def test_offers_the_wizard_its_defaults(self, client: TestClient) -> None:
        body = client.get("/api/v1/setup/status").json()

        assert body["default_data_dir"]
        assert body["default_thumb_dir"]
        assert body["current_data_dir"] == str(_overlay["data_dir"])

    def test_closes_setup_for_existing_users(
        self, client: TestClient, db_session: Session
    ) -> None:
        build_user(db_session, "legacy")

        assert client.get("/api/v1/setup/status").json()["configured"] is True

    def test_needs_no_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/setup/status").status_code == 200

    def test_reports_only_configured_once_set_up(self, client: TestClient) -> None:
        _complete(client)

        body = client.get("/api/v1/setup/status").json()

        assert body == {
            "configured": True,
            "setup_available": False,
            "recovery_required": False,
            "user_count": 0,
        }

    def test_redacts_internal_storage_details_once_configured(
        self, client: TestClient
    ) -> None:
        _complete(client, storage_backend="s3", s3_bucket="private-bucket")

        text = client.get("/api/v1/setup/status").text

        assert "private-bucket" not in text
        assert str(_overlay["data_dir"]) not in text

    def test_requires_recovery_when_a_completed_install_has_no_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        runtime_config.mark_configured(db_session)

        body = client.get("/api/v1/setup/status").json()

        assert body["recovery_required"] is True, (
            "a completion marker never reopens first ownership"
        )


class TestCompleteSetup:
    def test_rejects_a_missing_browser_session(self, client: TestClient) -> None:
        client.cookies.clear()

        response = client.post("/api/v1/setup", json=_payload())

        assert response.status_code == 403, response.text

    def test_keeps_the_account_when_storage_preparation_fails(
        self, client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.services.storage_backend.enroll_legacy_local_root",
            lambda *args, **kwargs: False,
        )

        response = _complete(client)

        assert response.json()["storage_ready"] is False
        assert db_session.exec(select(User)).one().username == USERNAME

    def test_retries_pending_storage_with_the_created_account(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with monkeypatch.context() as patch:
            patch.setattr(
                "app.services.storage_backend.enroll_legacy_local_root",
                lambda *args, **kwargs: False,
            )
            _complete(client)

        response = client.post("/api/v1/setup/prepare-storage")

        assert response.json()["ready"] is True, response.text

    def test_storage_preparation_retry_is_idempotent(self, client: TestClient) -> None:
        _complete(client)
        client.post("/api/v1/setup/prepare-storage")

        response = client.post("/api/v1/setup/prepare-storage")

        assert response.json()["ready"] is True, response.text

    def test_preparation_retry_requires_authentication(
        self, client: TestClient
    ) -> None:
        response = client.post("/api/v1/setup/prepare-storage")

        assert response.status_code == 401, response.text

    def test_preparation_retry_requires_admin(
        self, client: TestClient, user_headers
    ) -> None:
        response = client.post("/api/v1/setup/prepare-storage", headers=user_headers())

        assert response.status_code == 403, response.text

    def test_storage_check_does_not_create_an_account(
        self, client: TestClient, db_session: Session
    ) -> None:
        response = client.post(
            "/api/v1/setup/check-storage", json={"storage_backend": "local"}
        )

        assert response.json()["ready"] is True, response.text
        assert db_session.exec(select(User)).first() is None

    def test_storage_check_rejects_a_populated_directory(
        self, client: TestClient, runtime_dirs: Path
    ) -> None:
        directory = runtime_dirs / "files"
        directory.mkdir()
        (directory / "existing.stl").write_bytes(b"existing")

        response = client.post(
            "/api/v1/setup/check-storage", json={"storage_backend": "local"}
        )

        assert response.json()["detail"] == "data_dir_not_empty", response.text

    def test_creates_an_admin_through_browser_preparation(
        self, client: TestClient
    ) -> None:
        _overlay["setup_mode"] = "trusted_network"
        _overlay["setup_allowed_hosts"] = "testserver"
        client.headers["Origin"] = "http://testserver"
        preparation = client.post("/api/v1/setup/session")
        client.headers["X-PrintStash-Setup-CSRF"] = preparation.json()["csrf"]

        response = client.post(
            "/api/v1/setup", json={"username": USERNAME, "password": PASSWORD}
        )

        assert response.status_code == 201, response.text

    @pytest.mark.parametrize(
        "origin",
        ["https://attacker.example", "null", "http://testserver:8888"],
        ids=["foreign", "opaque", "wrong-port"],
    )
    def test_rejects_foreign_preparation(self, client: TestClient, origin: str) -> None:
        _overlay["setup_mode"] = "trusted_network"
        _overlay["setup_allowed_hosts"] = "testserver"

        response = client.post("/api/v1/setup/session", headers={"Origin": origin})

        assert response.status_code == 403, response.text

    def test_rejects_preparation_on_an_untrusted_host(self, client: TestClient) -> None:
        _overlay["setup_mode"] = "trusted_network"

        response = client.post(
            "/api/v1/setup/session",
            headers={"Host": "attacker.example", "Origin": "http://attacker.example"},
        )

        assert response.status_code == 403, response.text

    def test_refuses_preparation_after_a_completion_marker(
        self, client: TestClient, make_system_config
    ) -> None:
        from datetime import datetime, timezone

        _overlay["setup_mode"] = "trusted_network"
        _overlay["setup_allowed_hosts"] = "testserver"
        make_system_config(configured_at=datetime(2026, 1, 1, tzinfo=timezone.utc))

        response = client.post(
            "/api/v1/setup/session", headers={"Origin": "http://testserver"}
        )

        assert response.status_code == 409, response.text

    def test_local_setup_rebinds_a_writable_backend(
        self, client: TestClient, runtime_dirs: Path
    ) -> None:
        from app.services.storage_backend import (
            LocalStorageBackend,
            bind_backend,
            get_backend,
        )

        startup_backend = LocalStorageBackend()
        startup_backend.ensure_setup()
        assert startup_backend.recovery_mode is True
        bind_backend(startup_backend)

        response = _complete(client)

        assert response.status_code == 201, response.text
        active_backend = get_backend()
        assert active_backend is not startup_backend
        assert active_backend.recovery_mode is False
        destination = runtime_dirs / "files" / "post-setup.bin"
        active_backend.create_bytes(b"ready", str(destination))
        assert destination.read_bytes() == b"ready"

    def test_provisions_an_initial_sftp_root_before_persisting_config(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        class _Backend:
            def __init__(self, _transport: object) -> None:
                pass

            def provision_root(self) -> None:
                calls.append("provision")

        monkeypatch.setattr(
            "app.services.storage_opendal.OpenDALStorageBackend", _Backend
        )

        response = client.post("/api/v1/setup", json=_sftp_payload())

        assert response.status_code == 201, response.text
        assert calls == ["provision"]

    def test_sftp_provision_failure_leaves_setup_unmodified(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        before = db_session.get(SystemConfig, 1)
        before_state = before.model_dump() if before is not None else None

        class _Backend:
            def __init__(self, _transport: object) -> None:
                pass

            def provision_root(self) -> None:
                raise OSError("remote root is not writable")

        monkeypatch.setattr(
            "app.services.storage_opendal.OpenDALStorageBackend", _Backend
        )

        response = client.post("/api/v1/setup", json=_sftp_payload())

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "sftp_root_not_provisionable"
        assert db_session.exec(select(User)).first() is None
        after = db_session.get(SystemConfig, 1)
        assert (after.model_dump() if after is not None else None) == before_state

    def test_invalid_browser_session_never_provisions_sftp_root(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[str] = []
        before = db_session.get(SystemConfig, 1)
        before_state = before.model_dump() if before is not None else None

        class _Backend:
            def __init__(self, _transport: object) -> None:
                calls.append("construct")

            def provision_root(self) -> None:
                calls.append("provision")

        monkeypatch.setattr(
            "app.services.storage_opendal.OpenDALStorageBackend", _Backend
        )

        response = client.post(
            "/api/v1/setup",
            json=_sftp_payload(),
            headers={"X-PrintStash-Setup-CSRF": "invalid"},
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "setup_session_expired"
        assert calls == []
        assert db_session.exec(select(User)).first() is None
        after = db_session.get(SystemConfig, 1)
        assert (after.model_dump() if after is not None else None) == before_state

    def test_creates_the_first_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        response = _complete(client)

        assert response.status_code == 201, response.text
        user = db_session.exec(select(User)).one()
        assert user.username == USERNAME
        assert user.is_superuser is True

    def test_returns_a_usable_admin_token(self, client: TestClient) -> None:
        token = _complete(client).json()["access_token"]

        probe = client.get(
            "/api/v1/health/details", headers={"Authorization": f"Bearer {token}"}
        )

        assert probe.status_code == 200, "the wizard's token must skip a second login"

    def test_sets_the_session_cookie(self, client: TestClient) -> None:
        response = _complete(client)

        assert response.cookies, "the browser flows straight into the app"

    def test_marks_the_vault_configured(self, client: TestClient) -> None:
        _complete(client)

        assert client.get("/api/v1/setup/status").json()["configured"] is True

    def test_pins_the_local_storage_roots(
        self, client: TestClient, db_session: Session, runtime_dirs: Path
    ) -> None:
        _complete(client)

        config = db_session.get(SystemConfig, 1)
        assert config is not None
        # Left null, a later environment change would reinterpret existing rows
        # against a different mount.
        assert config.data_dir == str((runtime_dirs / "files").resolve())
        assert config.thumb_dir == str((runtime_dirs / "thumbs").resolve())

    def test_persists_every_storage_choice_from_setup(
        self, client: TestClient, db_session: Session
    ) -> None:
        response = _complete(
            client,
            storage_backend="s3",
            s3_bucket="vault-assets",
            s3_endpoint_url="https://r2.example.test",
            s3_region="auto",
            s3_access_key="asset-key",
            s3_secret_key="asset-secret",
            backup_retention_days=14,
            backup_s3_bucket="vault-backups",
            backup_s3_endpoint_url="https://backup-r2.example.test",
            backup_s3_region="auto",
            backup_s3_access_key="backup-key",
            backup_s3_secret_key="backup-secret",
        )

        assert response.status_code == 201, response.text
        assert response.json()["storage_backend"] == "s3"
        config = db_session.get(SystemConfig, 1)
        assert config is not None
        assert config.s3_bucket == "vault-assets"
        assert config.backup_retention_days == 14
        assert config.backup_s3_bucket == "vault-backups"

    def test_refuses_a_second_run(self, client: TestClient) -> None:
        _complete(client)

        second = _complete(client)

        assert second.status_code == 409, second.text
        assert second.json()["detail"] == "already_configured"

    def test_creates_no_second_user_on_a_refused_run(
        self, client: TestClient, db_session: Session
    ) -> None:
        _complete(client)
        _complete(client, username="second-admin")

        assert len(db_session.exec(select(User)).all()) == 1

    def test_refuses_when_a_user_already_exists(
        self, client: TestClient, db_session: Session
    ) -> None:
        build_user(db_session, "legacy-admin")

        response = _complete(client)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "users_already_exist"

    def test_rejects_a_wrong_browser_proof(
        self, client: TestClient, db_session: Session
    ) -> None:
        response = client.post(
            "/api/v1/setup",
            json=_payload(),
            headers={"X-PrintStash-Setup-CSRF": "invalid"},
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "setup_session_expired"
        assert db_session.exec(select(User)).first() is None

    def test_rejects_a_storage_backend_it_does_not_implement(
        self, client: TestClient
    ) -> None:
        response = _complete(client, storage_backend="ftp")

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_storage_backend"

    def test_requires_a_bucket_when_s3_is_selected(self, client: TestClient) -> None:
        response = _complete(client, storage_backend="s3")

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "s3_bucket_required"

    def test_rejects_an_unknown_field(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/setup", json=_payload(unexpected="ignored-before-hardening")
        )

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({"username": "ab"}, id="username-under-3"),
            pytest.param({"password": "short"}, id="password-under-8"),
        ],
    )
    def test_rejects_a_credential_below_its_length_bound(
        self, client: TestClient, overrides: dict[str, str]
    ) -> None:
        assert _complete(client, **overrides).status_code == 422


class TestStorageValidation:
    def test_refuses_a_populated_vault_directory(self, client: TestClient) -> None:
        existing = Path(_overlay["data_dir"]) / "Jonathan" / "part.stl"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"user-owned")

        response = _complete(client)

        assert response.status_code == 400, response.text
        # The frontend omits unchanged defaults, so the *effective* path is what is
        # validated — not only an explicit override.
        assert response.json()["detail"] == "data_dir_not_empty"

    def test_leaves_a_populated_directory_untouched(self, client: TestClient) -> None:
        existing = Path(_overlay["data_dir"]) / "Jonathan" / "part.stl"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"user-owned")

        _complete(client)

        assert existing.read_bytes() == b"user-owned"

    def test_refuses_nested_storage_roots(
        self, client: TestClient, runtime_dirs: Path
    ) -> None:
        shared = runtime_dirs / "shared"

        response = _complete(
            client, data_dir=str(shared), thumb_dir=str(shared / "thumbs")
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "storage_paths_overlap"

    def test_refuses_roots_aliased_by_a_symlink(
        self, client: TestClient, runtime_dirs: Path
    ) -> None:
        shared = runtime_dirs / "shared"
        shared.mkdir()
        alias = runtime_dirs / "alias"
        alias.symlink_to(shared, target_is_directory=True)

        response = _complete(client, data_dir=str(shared), thumb_dir=str(alias))

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "storage_paths_overlap"

    @pytest.mark.parametrize("managed_root", ["staging_dir", "backup_dir"], ids=str)
    def test_refuses_a_root_that_swallows_a_managed_scratch_root(
        self, client: TestClient, managed_root: str
    ) -> None:
        response = _complete(client, data_dir=str(_overlay[managed_root]))

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "storage_paths_overlap"

    def test_refuses_a_root_that_swallows_the_database_file(
        self, client: TestClient, runtime_dirs: Path
    ) -> None:
        # A vault root containing the SQLite file would put the database inside the
        # blob store the GC walks.
        _overlay["db_url"] = f"sqlite:///{runtime_dirs / 'files' / 'vault.sqlite'}"

        response = _complete(client)

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "storage_paths_overlap"

    @pytest.mark.parametrize(
        ("failing_call", "detail"),
        [
            pytest.param("resolve", "invalid_data_dir_path", id="unresolvable"),
            pytest.param("mkdir", "data_dir_not_creatable", id="not-creatable"),
            pytest.param("iterdir", "data_dir_not_readable", id="not-readable"),
        ],
    )
    def test_reports_a_root_the_filesystem_refuses(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        failing_call: str,
        detail: str,
    ) -> None:
        # A real filesystem cannot be made to fail these on demand, so only the one
        # call under test is stood in for — bound in the setup module's namespace, so
        # the pathlib.Path every other module holds is untouched.
        import app.api.v1.setup as setup_mod

        monkeypatch.setattr(setup_mod, "Path", _hostile_path(failing_call))

        response = _complete(client)

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == detail

    def test_completes_when_the_write_probe_cannot_be_removed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Probe cleanup is best-effort: a filesystem that refuses the unlink must not
        # fail an otherwise valid setup.
        import app.api.v1.setup as setup_mod

        monkeypatch.setattr(setup_mod, "Path", _hostile_path("unlink"))

        response = _complete(client)

        assert response.status_code == 201, response.text

    def test_refuses_a_read_only_root(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def read_only_mount(*_args: object, **_kwargs: object):
            raise PermissionError("read-only mount")

        monkeypatch.setattr(
            "app.api.v1.setup.tempfile.NamedTemporaryFile", read_only_mount
        )

        response = _complete(client)

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "data_dir_not_writable"

    @pytest.mark.parametrize(
        "rejection",
        [
            pytest.param("populated", id="populated-vault-dir"),
            pytest.param("nested", id="nested-roots"),
            pytest.param("managed", id="swallows-staging"),
        ],
    )
    def test_creates_no_user_when_validation_fails(
        self,
        client: TestClient,
        db_session: Session,
        runtime_dirs: Path,
        rejection: str,
    ) -> None:
        if rejection == "populated":
            existing = Path(_overlay["data_dir"]) / "part.stl"
            existing.parent.mkdir(parents=True, exist_ok=True)
            existing.write_bytes(b"user-owned")
            overrides: dict[str, Any] = {}
        elif rejection == "nested":
            shared = runtime_dirs / "shared"
            overrides = {
                "data_dir": str(shared),
                "thumb_dir": str(shared / "thumbs"),
            }
        else:
            overrides = {"data_dir": str(_overlay["staging_dir"])}

        _complete(client, **overrides)

        assert db_session.exec(select(User)).first() is None, (
            "validation runs before the database is touched"
        )


class TestFinalizationRollback:
    @pytest.fixture
    def failed_finalization(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        """Drive a setup that dies after the user and config rows are staged."""

        def injected(*_args: object, **_kwargs: object):
            raise RuntimeError("injected setup failure")

        overlay_before = dict(_overlay)
        monkeypatch.setattr(runtime_config, "mark_configured", injected)
        with pytest.raises(RuntimeError, match="injected setup failure"):
            _complete(client, storage_backend="s3", s3_bucket="must-rollback")
        return overlay_before

    def test_rolls_back_the_user(
        self, failed_finalization: dict, db_session: Session
    ) -> None:
        db_session.expire_all()

        assert db_session.exec(select(User)).first() is None

    def test_rolls_back_the_config_row(
        self, failed_finalization: dict, db_session: Session
    ) -> None:
        db_session.expire_all()

        assert db_session.get(SystemConfig, 1) is None

    def test_leaves_the_runtime_overlay_untouched(
        self, failed_finalization: dict
    ) -> None:
        assert _overlay == failed_finalization


class TestBrowserPreparation:
    def test_cookie_is_not_accessible_to_javascript(self, client):
        response = client.post("/api/v1/setup/session")
        assert "HttpOnly" in response.headers["set-cookie"]

    def test_cookie_is_restricted_to_same_site(self, client):
        response = client.post("/api/v1/setup/session")
        assert "SameSite=strict" in response.headers["set-cookie"]

    def test_preparation_expires_after_an_hour(self, client):
        response = client.post("/api/v1/setup/session")
        assert "Max-Age=3600" in response.headers["set-cookie"]

    def test_cookie_is_secure_over_https(self, client):
        response = client.post(
            "https://localhost/api/v1/setup/session",
            headers={"Origin": "https://localhost"},
        )
        assert "Secure" in response.headers["set-cookie"]

    def test_preparation_can_be_renewed_without_reserving_an_account(self, client):
        renewed = client.post("/api/v1/setup/session")
        client.headers["X-PrintStash-Setup-CSRF"] = renewed.json()["csrf"]
        assert _complete(client).status_code == 201

    def test_old_csrf_does_not_match_a_renewed_cookie(self, client):
        client.post("/api/v1/setup/session")
        assert _complete(client).status_code == 403

    def test_preparation_cannot_authenticate_an_admin_request(self, client):
        assert client.post("/api/v1/setup/prepare-storage").status_code == 401

    def test_storage_check_does_not_create_configuration(self, client, db_session):
        client.post("/api/v1/setup/check-storage", json={"storage_backend": "local"})
        assert db_session.get(SystemConfig, 1) is None

    @pytest.mark.parametrize(
        "origin",
        [
            "null",
            "",
            "http://testserver:81",
            "https://testserver",
            "http://testserver/",
            "http://testserver@evil.local",
            "http://testserver?x=1",
        ],
    )
    def test_invalid_origin_cannot_start_a_session(self, client, origin):
        response = client.post("/api/v1/setup/session", headers={"Origin": origin})
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "192.168.1.2",
            "printer.local",
            "vault.home.arpa",
            "[::1]",
            "[fd00::1]",
        ],
    )
    def test_trusted_hosts_can_prepare_setup_with_a_port(self, client, host):
        response = client.post(
            "/api/v1/setup/session",
            headers={"Host": f"{host}:8080", "Origin": f"http://{host}:8080"},
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "host", ["public.example", "127.0.0.1.evil.example", "8.8.8.8", "100.64.0.1"]
    )
    def test_unapproved_host_cannot_prepare_setup(self, client, host):
        response = client.post(
            f"http://{host}/api/v1/setup/session", headers={"Origin": f"http://{host}"}
        )
        assert response.status_code == 403

    def test_expired_preparation_is_rejected(self, client):
        from datetime import timedelta

        import jwt

        from app.core.time import utcnow
        from app.services import setup_bootstrap

        client.cookies.clear()
        ticket = jwt.encode(
            {
                "aud": "printstash-setup",
                "iat": utcnow() - timedelta(hours=2),
                "exp": utcnow() - timedelta(hours=1),
                "csrf": "expired",
            },
            setup_bootstrap._signing_key(),
            algorithm="HS256",
        )
        client.cookies.set(setup_bootstrap.COOKIE, ticket, path="/api/v1/setup")
        client.headers[setup_bootstrap.CSRF_HEADER] = "expired"
        assert _complete(client).status_code == 403

    def test_failure_before_commit_rolls_back_account(
        self, client, db_session, monkeypatch
    ):
        def fail_commit(self):
            raise RuntimeError("database write failed")

        with monkeypatch.context() as patch:
            patch.setattr(Session, "commit", fail_commit)
            with pytest.raises(RuntimeError, match="database write failed"):
                _complete(client)
        assert db_session.exec(select(User)).all() == []


class TestLibraryLocationDiscovery:
    def test_requires_an_administrator(self, client):
        assert client.get("/api/v1/libraries/locations").status_code == 401

    def test_hides_managed_storage_mounts(
        self, client, auth_headers, monkeypatch, runtime_dirs
    ):
        directory = runtime_dirs / "files"
        directory.mkdir()
        monkeypatch.setattr(
            "app.services.library_locations.mounted_directories", lambda: [directory]
        )
        assert (
            client.get("/api/v1/libraries/locations", headers=auth_headers).json() == []
        )

    def test_offers_a_mounted_folder_before_enabling_sources(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        directory = tmp_path / "models-to-connect"
        directory.mkdir()
        monkeypatch.setattr(
            "app.services.library_locations.mounted_directories", lambda: [directory]
        )
        response = client.get("/api/v1/libraries/locations", headers=auth_headers)
        assert response.json() == [str(directory)]


class TestPreparationFailures:
    @pytest.mark.parametrize("secret", ["", "changeme_jwt_secret_please_change"])
    def test_unprepared_signing_key_cannot_issue_a_browser_session(
        self, client, secret
    ):
        _overlay["jwt_secret"] = secret
        assert client.post("/api/v1/setup/session").status_code == 503

    def test_missing_csrf_proof_cannot_create_an_account(self, client):
        del client.headers["X-PrintStash-Setup-CSRF"]
        assert _complete(client).status_code == 403

    def test_ipv4_mapped_loopback_can_prepare_setup(self, client):
        response = client.post(
            "/api/v1/setup/session",
            headers={
                "Host": "[::ffff:127.0.0.1]:8080",
                "Origin": "http://[::ffff:127.0.0.1]:8080",
            },
        )
        assert response.status_code == 200

    def test_invalid_port_is_rejected(self, client):
        response = client.post(
            "/api/v1/setup/session", headers={"Origin": "http://testserver:invalid"}
        )
        assert response.status_code == 403

    def test_scoped_ipv6_address_is_not_an_allowed_origin(self, client):
        response = client.post(
            "/api/v1/setup/session",
            headers={"Host": "[fe80::1%25eth0]", "Origin": "http://[fe80::1%25eth0]"},
        )
        assert response.status_code == 403

    def test_preparation_cookie_does_not_appear_in_application_logs(
        self, client, caplog
    ):
        from app.services.setup_bootstrap import COOKIE

        client.post("/api/v1/setup/session")
        assert client.cookies.get(COOKIE) not in caplog.text


class TestRemoteStorageProbe:
    @pytest.mark.s3
    def test_checks_selected_s3_storage_without_changing_the_live_backend(
        self, client, db_session
    ):
        from uuid import uuid4

        import boto3
        from botocore.config import Config

        from app.services.storage_backend import get_backend
        from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_endpoint

        endpoint = s3_endpoint()
        remote = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}),
        )
        bucket = f"setup-check-{uuid4().hex}"
        remote.create_bucket(Bucket=bucket)
        active_backend = get_backend()
        try:
            response = client.post(
                "/api/v1/setup/check-storage",
                json={
                    "storage_provider": "s3_self_hosted",
                    "storage_provider_config": {
                        "provider": "s3_self_hosted",
                        "bucket": bucket,
                        "endpoint_url": endpoint,
                        "region": "us-east-1",
                        "access_key": S3_ACCESS_KEY,
                        "secret_key": S3_SECRET_KEY,
                    },
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["ready"] is True
            assert get_backend() is active_backend
            assert db_session.exec(select(User)).all() == []
        finally:
            remote.delete_bucket(Bucket=bucket)
            remote.close()


class TestRecoveryPermissions:
    def test_read_scoped_administrator_cannot_prepare_storage(
        self, client, user_headers
    ):
        response = client.post(
            "/api/v1/setup/prepare-storage",
            headers=user_headers(is_superuser=True, scope="read"),
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "insufficient_scope"

    def test_existing_account_without_marker_requires_recovery(self, client, make_user):
        make_user(superuser=True)
        response = client.get("/api/v1/setup/status")
        assert response.json()["configured"] is True
        assert response.json()["recovery_required"] is True
        assert response.json()["setup_available"] is False


class TestRuntimeActivationRecovery:
    def test_activation_failure_keeps_a_usable_account(self, client, monkeypatch):
        def unavailable(config):
            raise RuntimeError("runtime activation temporarily unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(runtime_config, "activate_config", unavailable)
            response = _complete(client)
        assert response.status_code == 201
        assert response.json()["storage_ready"] is False
        recovered = client.post(
            "/api/v1/setup/prepare-storage",
            headers={"Authorization": f"Bearer {response.json()['access_token']}"},
        )
        assert recovered.status_code == 200
        assert recovered.json()["ready"] is True


class TestSetupRequestLimits:
    @pytest.mark.parametrize(
        "path,limit,previous",
        [("/session", 30, 1), ("/check-storage", 20, 0), ("", 20, 0)],
    )
    def test_rejects_a_burst_after_the_endpoint_limit(
        self, client, path, limit, previous
    ):
        for _ in range(limit - previous):
            response = client.post(
                f"/api/v1/setup{path}",
                json=_payload() if not path else {},
                headers={"Origin": "https://untrusted.example"},
            )
            assert response.status_code == 403
        response = client.post(
            f"/api/v1/setup{path}",
            json=_payload() if not path else {},
            headers={"Origin": "https://untrusted.example"},
        )
        assert response.status_code == 429
        assert response.json()["detail"] == "rate_limited"


class TestLibraryLocationPermissions:
    def test_member_cannot_discover_server_mounts(self, client, user_headers):
        response = client.get("/api/v1/libraries/locations", headers=user_headers())
        assert response.status_code == 403
        assert response.json()["detail"] == "admin_required"
