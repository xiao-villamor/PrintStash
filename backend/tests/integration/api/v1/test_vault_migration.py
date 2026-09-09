"""Migration control errors stay actionable without inventing absent runs."""

import pytest

from tests.factories import bearer, build_user


class TestMigrationControls:
    @pytest.mark.parametrize(
        "method,action,body",
        [
            ("POST", "pause", {}),
            ("POST", "retry", {}),
            ("POST", "retain", {"remove_credentials": False}),
            ("GET", "report", None),
        ],
    )
    def test_missing_run_controls_return_safe_conflict(
        self, client, db_session, method, action, body
    ):
        headers = bearer(build_user(db_session, superuser=True), scope="admin")
        response = client.request(
            method,
            f"/api/v1/storage/migrations/missing/{action}",
            headers=headers,
            **({"json": body} if body is not None else {}),
        )
        assert response.status_code == 409
        assert response.json() == {"detail": "migration_not_found"}

    def test_empty_history_is_readable(self, client, db_session):
        headers = bearer(build_user(db_session, superuser=True), scope="admin")
        response = client.get("/api/v1/storage/migrations", headers=headers)
        assert response.status_code == 200
        assert response.json() == []
