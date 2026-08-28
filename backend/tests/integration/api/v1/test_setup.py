"""The first-run wizard: the one unauthenticated write surface, and how it closes.

While the vault is unconfigured this router accepts a POST with no credentials at all —
it has to, because there is nobody to authenticate as yet. Three things keep that from
being a takeover: the operator token printed to the server log, the refusal to run twice,
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
from app.services.setup_token import current_setup_token
from tests.factories import build_user

USERNAME = "admin"
PASSWORD = "Password123"


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
        "setup_token": current_setup_token(),
        "username": USERNAME,
        "password": PASSWORD,
        "storage_backend": "local",
    }
    body.update(overrides)
    return body


def _complete(client: TestClient, **overrides: Any):
    return client.post("/api/v1/setup", json=_payload(**overrides))


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
    def test_reports_an_unconfigured_vault(self, client: TestClient) -> None:
        body = client.get("/api/v1/setup/status").json()

        assert body["configured"] is False
        assert body["setup_token_required"] is True

    def test_offers_the_wizard_its_defaults(self, client: TestClient) -> None:
        body = client.get("/api/v1/setup/status").json()

        assert body["default_data_dir"]
        assert body["default_thumb_dir"]
        assert body["current_data_dir"] == str(_overlay["data_dir"])

    def test_counts_existing_users(
        self, client: TestClient, db_session: Session
    ) -> None:
        build_user(db_session, "legacy")

        assert client.get("/api/v1/setup/status").json()["user_count"] == 1

    def test_needs_no_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/setup/status").status_code == 200

    def test_reports_only_configured_once_set_up(self, client: TestClient) -> None:
        _complete(client)

        body = client.get("/api/v1/setup/status").json()

        assert body == {"configured": True, "user_count": 0}

    def test_redacts_internal_storage_details_once_configured(
        self, client: TestClient
    ) -> None:
        _complete(client, storage_backend="s3", s3_bucket="private-bucket")

        text = client.get("/api/v1/setup/status").text

        assert "private-bucket" not in text
        assert str(_overlay["data_dir"]) not in text

    def test_stays_unconfigured_while_no_user_exists(
        self, client: TestClient, db_session: Session
    ) -> None:
        runtime_config.mark_configured(db_session)

        body = client.get("/api/v1/setup/status").json()

        assert body["configured"] is False, (
            "a stamped config with no user is a half-configured vault"
        )


class TestCompleteSetup:
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

    def test_rejects_a_wrong_operator_token(
        self, client: TestClient, db_session: Session
    ) -> None:
        response = _complete(client, setup_token="attacker-controlled-token")

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "invalid_setup_token"
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
            pytest.param({"setup_token": "short"}, id="token-under-16"),
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
