"""The configuration HTTP contract binds edits to the state the administrator reviewed."""

from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlmodel import Session

from app.core.config import _overlay, settings


class TestEditing:
    def test_returns_an_etag_matching_the_configuration_editing_base(
        self, client, auth_headers
    ):
        response = client.get("/api/v1/config", headers=auth_headers)
        assert response.status_code == 200
        row = response.json()
        assert (
            response.headers["etag"]
            == f'"vault-config-e{row["edit_epoch"]}-v{row["edit_version"]}"'
        )
        assert row["edit_version"] > 0

    def test_reads_committed_editable_values_before_runtime_publication(
        self, client, auth_headers, make_system_config
    ):
        row = make_system_config(oidc_client_id="committed-client")
        _overlay["oidc_client_id"] = "old-process-value"
        response = client.get("/api/v1/config", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["oidc_client_id"] == "committed-client"
        assert response.json()["edit_version"] == row.vault_edit_version

    def test_clears_an_override_without_returning_the_retired_runtime_value(
        self, client, auth_headers, make_system_config, db_session
    ):
        make_system_config(oidc_client_id="old-client")
        _overlay["oidc_client_id"] = "old-client"
        db_session.execute(
            text("UPDATE system_config SET oidc_client_id=NULL WHERE id=1")
        )
        db_session.commit()
        response = client.get("/api/v1/config", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["oidc_client_id"] == settings.frozen.oidc_client_id
        assert response.json()["edit_version"] > 1

    def test_commits_an_edit_from_the_reviewed_base(self, client, auth_headers):
        before = client.get("/api/v1/config", headers=auth_headers)
        response = client.put(
            "/api/v1/config",
            headers={
                **auth_headers,
                "If-Match": before.headers["etag"],
                "X-PrintStash-Edit-Contract": "conditional-v1",
            },
            json={"currency": "EUR"},
        )
        assert response.status_code == 200, response.text
        row = response.json()
        assert row["currency"] == "EUR"
        assert row["edit_version"] > before.json()["edit_version"]
        assert (
            response.headers["etag"]
            == f'"vault-config-e{row["edit_epoch"]}-v{row["edit_version"]}"'
        )

    def test_rejects_an_outdated_compound_patch(self, client, auth_headers):
        before = client.get("/api/v1/config", headers=auth_headers)
        winner = client.put(
            "/api/v1/config", headers=auth_headers, json={"currency": "GBP"}
        )
        assert winner.status_code == 200
        response = client.put(
            "/api/v1/config",
            headers={
                **auth_headers,
                "If-Match": before.headers["etag"],
                "X-PrintStash-Edit-Contract": "conditional-v1",
            },
            json={
                "currency": "EUR",
                "auto_mark_known_good": False,
                "automatic_backups_enabled": True,
            },
        )
        assert response.status_code == 412, response.text
        assert response.json()["detail"] == "edit_conflict"
        current = client.get("/api/v1/config", headers=auth_headers)
        assert current.json() == winner.json()

    def test_requires_the_opted_in_editing_base(self, client, auth_headers):
        response = client.put(
            "/api/v1/config",
            headers={
                **auth_headers,
                "X-PrintStash-Edit-Contract": "conditional-v1",
            },
            json={"currency": "EUR"},
        )
        assert response.status_code == 428, response.text
        assert response.json()["detail"] == "edit_precondition_required"
        assert (
            client.get("/api/v1/config", headers=auth_headers).json()["currency"]
            == "USD"
        )

    def test_keeps_explicit_legacy_write_compatibility(self, client, auth_headers):
        before = client.get("/api/v1/config", headers=auth_headers)
        response = client.put(
            "/api/v1/config", headers=auth_headers, json={"currency": "EUR"}
        )
        assert response.status_code == 200
        assert response.json()["currency"] == "EUR"
        assert response.json()["edit_version"] > before.json()["edit_version"]

    @pytest.mark.parametrize(
        ("headers", "status"),
        [
            pytest.param({"If-Match": "*"}, 412, id="wildcard"),
            pytest.param({"If-Match": 'W/"weak"'}, 412, id="weak"),
            pytest.param(
                {"If-Match": '"model-1-e' + "0" * 32 + '-v1"'},
                412,
                id="wrong-aggregate",
            ),
            pytest.param({"If-Match": '"invalid"'}, 412, id="malformed"),
            pytest.param(
                {"If-Match": '"vault-config-e' + "0" * 32 + '-v0"'},
                412,
                id="zero-version",
            ),
            pytest.param(
                {"If-Match": '"vault-config-e' + "0" * 32 + '-v-1"'},
                412,
                id="negative-version",
            ),
            pytest.param(
                {"If-Match": '"vault-config-e' + "0" * 32 + '-v9223372036854775808"'},
                412,
                id="version-overflow",
            ),
            pytest.param(
                {"If-Match": '"vault-config-e' + "0" * 32 + "-v" + "9" * 5000 + '"'},
                412,
                id="oversized-digits",
            ),
            pytest.param(
                {"X-PrintStash-Edit-Contract": "unknown"}, 400, id="unknown-contract"
            ),
        ],
    )
    def test_rejects_malformed_or_unsupported_preconditions(
        self, client, auth_headers, headers, status
    ):
        response = client.put(
            "/api/v1/config",
            headers={**auth_headers, **headers},
            json={"currency": "EUR"},
        )
        assert response.status_code == status, response.text
        assert (
            client.get("/api/v1/config", headers=auth_headers).json()["currency"]
            == "USD"
        )

    def test_rejects_a_base_from_a_retired_database_history(
        self, client, auth_headers, db_session
    ):
        before = client.get("/api/v1/config", headers=auth_headers)
        db_session.execute(
            text("UPDATE library_revision SET epoch=:epoch WHERE id=1"),
            {"epoch": uuid4().hex},
        )
        db_session.commit()
        response = client.put(
            "/api/v1/config",
            headers={**auth_headers, "If-Match": before.headers["etag"]},
            json={"currency": "EUR"},
        )
        assert response.status_code == 412
        assert (
            client.get("/api/v1/config", headers=auth_headers).json()["currency"]
            == "USD"
        )

    @pytest.mark.parametrize(
        ("field", "initial", "accepted", "later"),
        [
            pytest.param("currency", "USD", "EUR", "GBP", id="currency"),
            pytest.param(
                "oidc_client_id",
                "original",
                "accepted-client",
                "later-client",
                id="oidc-runtime",
            ),
        ],
    )
    def test_returns_the_committed_commands_own_receipt(
        self,
        client,
        auth_headers,
        db_session,
        make_system_config,
        field,
        initial,
        accepted,
        later,
    ):
        make_system_config(**{field: initial})
        before = client.get("/api/v1/config", headers=auth_headers)
        engine = db_session.get_bind()
        advanced = False

        def later_writer(session):
            nonlocal advanced
            if advanced:
                return
            advanced = True
            with engine.begin() as connection:
                connection.execute(
                    text(f"UPDATE system_config SET {field}=:value WHERE id=1"),
                    {"value": later},
                )

        event.listen(Session, "after_commit", later_writer)
        try:
            response = client.put(
                "/api/v1/config",
                headers={**auth_headers, "If-Match": before.headers["etag"]},
                json={field: accepted},
            )
        finally:
            event.remove(Session, "after_commit", later_writer)
        assert response.status_code == 200, response.text
        assert advanced
        assert response.json()[field] == accepted
        current = client.get("/api/v1/config", headers=auth_headers)
        assert current.json()[field] == later
        assert current.json()["edit_version"] > response.json()["edit_version"]

        if field == "oidc_client_id":
            assert settings.oidc_client_id == later

    def test_publishes_an_explicitly_cleared_runtime_override(
        self, client, auth_headers
    ):
        saved = client.put(
            "/api/v1/config", headers=auth_headers, json={"oidc_client_id": "temporary"}
        )
        assert saved.status_code == 200
        assert settings.oidc_client_id == "temporary"
        cleared = client.put(
            "/api/v1/config",
            headers={**auth_headers, "If-Match": saved.headers["etag"]},
            json={"oidc_client_id": ""},
        )
        assert cleared.status_code == 200
        assert settings.oidc_client_id == settings.frozen.oidc_client_id
