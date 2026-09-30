"""Administrator controls: omission, explicit null and strict Booleans."""

import pytest

from app.db.models import SystemConfig
from app.modules.derivatives.policy import SETTINGS


@pytest.mark.parametrize("name", list(SETTINGS.values()))
class TestDerivativeControls:
    def test_boolean_sets_an_override(self, client, auth_headers, db_session, name):
        response = client.put(
            "/api/v1/config", headers=auth_headers, json={name: False}
        )
        assert response.status_code == 200, response.text
        assert response.json()[name] is False
        assert getattr(db_session.get(SystemConfig, 1), name) is False

    def test_null_clears_an_override(
        self, client, auth_headers, db_session, make_system_config, name
    ):
        make_system_config(**{name: False})
        response = client.put("/api/v1/config", headers=auth_headers, json={name: None})
        assert response.status_code == 200, response.text
        assert response.json()[name] is True
        db_session.expire_all()
        assert getattr(db_session.get(SystemConfig, 1), name) is None

    def test_omission_preserves_other_overrides(
        self, client, auth_headers, db_session, make_system_config, name
    ):
        other = next(candidate for candidate in SETTINGS.values() if candidate != name)
        make_system_config(**{other: False})
        response = client.put("/api/v1/config", headers=auth_headers, json={name: True})
        assert response.status_code == 200, response.text
        assert response.json()[other] is False

    @pytest.mark.parametrize("value", ["false", 0, 1, "invalid", [], {}])
    def test_rejects_malformed_booleans(
        self, client, auth_headers, db_session, name, value
    ):
        response = client.put(
            "/api/v1/config", headers=auth_headers, json={name: value}
        )
        assert response.status_code == 422
        assert db_session.get(SystemConfig, 1) is None

    def test_non_admin_cannot_change_controls(
        self, client, user_headers, db_session, name
    ):
        response = client.put(
            "/api/v1/config", headers=user_headers(), json={name: False}
        )
        assert response.status_code == 403
        assert db_session.get(SystemConfig, 1) is None

    def test_anonymous_cannot_change_controls(self, client, db_session, name):
        response = client.put("/api/v1/config", json={name: False})
        assert response.status_code == 401
        assert db_session.get(SystemConfig, 1) is None
