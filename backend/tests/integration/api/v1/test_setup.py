"""The first-run wizard: the one unauthenticated write surface, and how it closes.

While the vault is unconfigured this router accepts a POST with no credentials at all —
it has to, because there is nobody to authenticate as yet. Three things keep that from
being a takeover: the browser preparation session, the refusal to run twice,
and the refusal to run at all once a user exists. If any of them regresses, anyone who can
reach the port can seize an established vault, so those rows are the point of this file.

Storage validation itself lives in ``modules.administration.setup_storage`` and is
tested there. What this file keeps is its HTTP half: a refusal reaches the browser as
a 400 with its detail code, and it arrives *before* the database is touched, so no
user is created — not just a status code.

``prepare-storage`` is the one setup route an authenticated owner uses: it retries
storage that failed to activate, and lets an owner provisioned from
``VAULT_SETUP_ADMIN_*`` choose storage after signing in, from any host.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from printstash_core.mesh.preview_profile import PreviewLook
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.db.models import SystemConfig, User
from app.modules.administration import runtime_config
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


class TestSetupPreviewAppearance:
    example = "/api/v1/setup/preview-example?look=sage&floor_grid=false"

    def test_reports_current_preview_defaults(self, client: TestClient) -> None:
        status = client.get("/api/v1/setup/status").json()

        assert status["preview_appearance"] == {
            "preview_look": "sky",
            "preview_floor_grid": True,
        }
        assert status["preview_mode"] == "single"

    def test_renders_the_example_for_a_prepared_browser(
        self, client: TestClient
    ) -> None:
        client.headers.pop("X-PrintStash-Setup-CSRF")

        response = client.get(self.example)

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/webp"
        assert response.content[8:12] == b"WEBP"

    @pytest.mark.parametrize("cookie", [None, "invalid-ticket"])
    def test_requires_a_valid_setup_session(
        self, client: TestClient, cookie: str | None
    ) -> None:
        client.cookies.clear()
        if cookie is not None:
            client.cookies.set("printstash_setup", cookie)

        assert client.get(self.example).status_code == 403

    def test_rejects_the_example_when_setup_is_closed(
        self, client: TestClient, db_session: Session
    ) -> None:
        build_user(db_session, "existing-owner")

        assert client.get(self.example).status_code == 409

    def test_rejects_the_example_when_setup_is_disabled(
        self, client: TestClient
    ) -> None:
        _overlay["setup_mode"] = "disabled"

        assert client.get(self.example).status_code == 403

    def test_rejects_the_example_for_an_untrusted_host(
        self, client: TestClient
    ) -> None:
        assert (
            client.get(self.example, headers={"Host": "public.example"}).status_code
            == 403
        )

    def test_persists_the_selected_appearance(
        self, client: TestClient, db_session: Session
    ) -> None:
        response = _complete(
            client,
            preview_appearance={"preview_look": "sage", "preview_floor_grid": False},
        )

        assert response.status_code == 201, response.text
        db_session.expire_all()
        config = db_session.get(SystemConfig, 1)
        assert config is not None
        assert config.preview_look.value == "sage"
        assert config.preview_floor_grid is False

    def test_omitting_appearance_retains_existing_preferences(
        self, client: TestClient, db_session: Session, make_system_config
    ) -> None:
        make_system_config(
            preview_look=PreviewLook.PLUM,
            preview_floor_grid=False,
            derivatives_previews_enabled=True,
            derivatives_previews_include_poles=False,
        )

        status = client.get("/api/v1/setup/status").json()
        assert status["preview_appearance"] == {
            "preview_look": "plum",
            "preview_floor_grid": False,
        }
        assert status["preview_mode"] == "oblique"
        response = _complete(client)

        assert response.status_code == 201, response.text
        db_session.expire_all()
        config = db_session.get(SystemConfig, 1)
        assert config is not None
        assert config.preview_look is PreviewLook.PLUM
        assert config.preview_floor_grid is False
        assert config.derivatives_previews_enabled is True
        assert config.derivatives_previews_include_poles is False

    @pytest.mark.parametrize(
        ("preview_mode", "enabled", "include_poles"),
        [
            ("single", False, False),
            ("oblique", True, False),
            ("oblique-poles", True, True),
        ],
    )
    def test_persists_the_selected_number_of_views(
        self,
        client: TestClient,
        db_session: Session,
        preview_mode: str,
        enabled: bool,
        include_poles: bool,
    ) -> None:
        response = _complete(client, preview_mode=preview_mode)

        assert response.status_code == 201, response.text
        db_session.expire_all()
        config = db_session.get(SystemConfig, 1)
        assert config is not None
        assert config.derivatives_previews_enabled is enabled
        assert config.derivatives_previews_include_poles is include_poles

    def test_rejects_unsupported_number_of_views(
        self, client: TestClient, db_session: Session
    ) -> None:
        assert _complete(client, preview_mode="unsupported").status_code == 422
        assert db_session.exec(select(User)).first() is None

    @pytest.mark.parametrize(
        "appearance",
        [
            {"preview_look": "unknown", "preview_floor_grid": False},
            {"preview_look": "sage", "preview_floor_grid": "no"},
        ],
    )
    def test_rejects_invalid_appearance_before_creating_the_owner(
        self, client: TestClient, db_session: Session, appearance: dict[str, object]
    ) -> None:
        assert _complete(client, preview_appearance=appearance).status_code == 422
        assert db_session.exec(select(User)).first() is None


@pytest.fixture
def provisioned_owner(
    auth_headers: dict[str, str], make_system_config
) -> dict[str, str]:
    """What startup leaves for ``VAULT_SETUP_ADMIN_*``: a signed-in owner, no storage."""
    make_system_config(
        configured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        setup_storage_pending=True,
    )
    return auth_headers


def _local_choice(root: Path) -> dict[str, str]:
    return {
        "storage_backend": "local",
        "data_dir": str(root / "chosen-files"),
        "thumb_dir": str(root / "chosen-thumbs"),
    }


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


class TestSetupStatus:
    def test_reports_registration_disabled_by_default(self, client: TestClient) -> None:
        _overlay["setup_mode"] = "disabled"
        response = client.get("/api/v1/setup/status")

        assert response.json()["setup_available"] is False

    def test_reports_an_unconfigured_vault(self, client: TestClient) -> None:
        body = client.get("/api/v1/setup/status").json()

        assert body["configured"] is False
        assert body["setup_available"] is True

    @pytest.mark.parametrize("role", ["data", "thumb"])
    def test_offers_the_deployment_path_as_the_wizard_default(
        self, client: TestClient, role: str
    ) -> None:
        # A runtime edit (the overlay this suite always sets) is not what a
        # blank field means: the deployment's own VAULT_DATA_ROOT layout is.
        body = client.get("/api/v1/setup/status").json()

        assert body[f"default_{role}_dir"] == str(
            getattr(settings.frozen, f"{role}_dir")
        )

    def test_reports_the_effective_library_path(self, client: TestClient) -> None:
        body = client.get("/api/v1/setup/status").json()

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
            "storage_choice_required": False,
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

    def test_reports_that_an_environment_owner_must_choose_storage(
        self, client: TestClient, provisioned_owner: dict[str, str]
    ) -> None:
        body = client.get("/api/v1/setup/status").json()

        assert body["storage_choice_required"] is True

    def test_reports_an_untrusted_host_as_the_reason(self, client: TestClient) -> None:
        body = client.get("http://public.example/api/v1/setup/status").json()

        assert body["unavailable_reason"] == "untrusted_host"

    def test_names_the_refused_host(self, client: TestClient) -> None:
        body = client.get("http://public.example/api/v1/setup/status").json()

        assert body["observed_host"] == "public.example"

    def test_reports_disabled_registration_as_the_reason(
        self, client: TestClient
    ) -> None:
        _overlay["setup_mode"] = "disabled"

        body = client.get("/api/v1/setup/status").json()

        assert body["unavailable_reason"] == "disabled"

    def test_omits_the_reason_while_registration_is_available(
        self, client: TestClient
    ) -> None:
        body = client.get("/api/v1/setup/status").json()

        assert "unavailable_reason" not in body

    def test_reports_environment_mode_as_the_reason(
        self, client: TestClient, environment_admin
    ) -> None:
        # The owner is created at startup; until then the browser has no door.
        environment_admin("store-owner", "StoreFormPassword123")

        body = client.get("/api/v1/setup/status").json()

        assert body["unavailable_reason"] == "environment"

    def test_reports_a_misconfiguration_as_the_reason(
        self, client: TestClient, environment_admin
    ) -> None:
        environment_admin("store-owner", "")

        body = client.get("/api/v1/setup/status").json()

        assert body["unavailable_reason"] == "admin_credentials_missing"

    def test_names_the_variables_to_fix(
        self, client: TestClient, environment_admin
    ) -> None:
        environment_admin("store-owner", "")

        body = client.get("/api/v1/setup/status").json()

        assert body["unavailable_variables"] == ["VAULT_SETUP_ADMIN_PASSWORD"]

    def test_never_reports_a_credential_value(
        self, client: TestClient, environment_admin
    ) -> None:
        # Credentials in the wrong mode are misconfigured, and the anonymous
        # status may name them but must not echo them.
        environment_admin("store-owner", "StoreFormPassword123", mode="trusted_network")

        text = client.get("/api/v1/setup/status").text

        assert "StoreFormPassword123" not in text


class TestPrepareStorage:
    def test_chooses_storage_for_an_environment_owner(
        self, client: TestClient, provisioned_owner: dict[str, str], tmp_path: Path
    ) -> None:
        response = client.post(
            "/api/v1/setup/prepare-storage",
            json=_local_choice(tmp_path),
            headers=provisioned_owner,
        )

        assert response.status_code == 200, response.text

    def test_closes_the_storage_choice(
        self, client: TestClient, provisioned_owner: dict[str, str], tmp_path: Path
    ) -> None:
        client.post(
            "/api/v1/setup/prepare-storage",
            json=_local_choice(tmp_path),
            headers=provisioned_owner,
        )

        body = client.get("/api/v1/setup/status").json()

        assert body["storage_choice_required"] is False

    def test_chooses_storage_from_an_untrusted_host(
        self, client: TestClient, provisioned_owner: dict[str, str], tmp_path: Path
    ) -> None:
        # A signed-in owner is already trusted. This is the path that still works
        # behind an app-store proxy that rewrites Host to a service name.
        response = client.post(
            "http://printstash-web/api/v1/setup/prepare-storage",
            json=_local_choice(tmp_path),
            headers=provisioned_owner,
        )

        assert response.status_code == 200, response.text

    def test_asks_for_a_choice_when_none_was_made(
        self, client: TestClient, provisioned_owner: dict[str, str]
    ) -> None:
        # Finishing unchosen storage would activate unpinned environment defaults.
        response = client.post(
            "/api/v1/setup/prepare-storage", headers=provisioned_owner
        )

        assert (response.status_code, response.json()["detail"]) == (
            409,
            "setup_storage_choice_required",
        )

    def test_treats_an_empty_body_as_no_choice(
        self, client: TestClient, provisioned_owner: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/setup/prepare-storage", json={}, headers=provisioned_owner
        )

        assert response.json()["detail"] == "setup_storage_choice_required"

    def test_refuses_a_choice_once_storage_was_chosen(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        token = _complete(client).json()["access_token"]

        response = client.post(
            "/api/v1/setup/prepare-storage",
            json=_local_choice(tmp_path / "elsewhere"),
            headers={"Authorization": f"Bearer {token}"},
        )

        assert (response.status_code, response.json()["detail"]) == (
            409,
            "setup_storage_already_chosen",
        )

    def test_reports_a_failed_activation_as_retryable(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The account and the choice are kept; the owner retries the same call.
        monkeypatch.setattr(
            "app.modules.storage.storage_backend.local.enroll_legacy_local_root",
            lambda *args, **kwargs: False,
        )
        token = _complete(client).json()["access_token"]

        response = client.post(
            "/api/v1/setup/prepare-storage",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert (response.status_code, response.json()["detail"]) == (
            503,
            "storage_root_enrollment_failed",
        )

    def test_reports_a_refused_choice(
        self, client: TestClient, provisioned_owner: dict[str, str], tmp_path: Path
    ) -> None:
        populated = tmp_path / "chosen-files"
        populated.mkdir()
        (populated / "someone-elses-model.stl").write_text("solid")

        response = client.post(
            "/api/v1/setup/prepare-storage",
            json=_local_choice(tmp_path),
            headers=provisioned_owner,
        )

        assert (response.status_code, response.json()["detail"]) == (
            400,
            "data_dir_not_empty",
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
            "app.modules.storage.storage_backend.local.enroll_legacy_local_root",
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
                "app.modules.storage.storage_backend.local.enroll_legacy_local_root",
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
        from app.modules.storage.storage_backend.local import LocalStorageBackend
        from app.modules.storage.storage_backend.runtime import (
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
            "app.modules.storage.storage_opendal.OpenDALStorageBackend", _Backend
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
            "app.modules.storage.storage_opendal.OpenDALStorageBackend", _Backend
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
            "app.modules.storage.storage_opendal.OpenDALStorageBackend", _Backend
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

    # Each refusal itself is tested where the validation lives:
    # tests/integration/modules/administration/test_setup_storage.py::TestPrepare.

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
        "host",
        [
            "public.example",
            "127.0.0.1.evil.example",
            "8.8.8.8",
            # Shared carrier-grade NAT space: other ISP customers, not a LAN.
            "100.64.0.1",
            # Tailscale Funnel can publish a tailnet name to the internet.
            "vault.tailnet-1234.ts.net",
        ],
    )
    def test_unapproved_host_cannot_prepare_setup(self, client, host):
        response = client.post(
            f"http://{host}/api/v1/setup/session", headers={"Origin": f"http://{host}"}
        )
        assert response.status_code == 403

    @pytest.mark.parametrize(
        ("username", "password", "mode"),
        [
            pytest.param("", "", "environment", id="environment-mode"),
            pytest.param(
                "store-owner",
                "StoreFormPassword123",
                "trusted_network",
                id="misconfigured",
            ),
        ],
    )
    def test_only_a_valid_trusted_network_policy_opens_the_browser_door(
        self, client, environment_admin, username, password, mode
    ):
        # Misconfigured settings fail closed: a half-filled install form must
        # never turn into first-come registration.
        environment_admin(username, password, mode=mode)

        response = client.post("/api/v1/setup/session")

        assert (response.status_code, response.json()["detail"]) == (
            403,
            "setup_disabled",
        )

    def test_expired_preparation_is_rejected(self, client):
        from datetime import timedelta

        import jwt

        from app.api import setup_session as setup_bootstrap
        from app.core.time import utcnow

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
            "app.modules.sources.library_locations.mounted_directories",
            lambda: [directory],
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
            "app.modules.sources.library_locations.mounted_directories",
            lambda: [directory],
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
        from app.api.setup_session import COOKIE

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

        from app.modules.storage.storage_backend.runtime import get_backend
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
