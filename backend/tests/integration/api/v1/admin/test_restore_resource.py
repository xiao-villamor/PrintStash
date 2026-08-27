"""Defends restore resource at the admin API integration boundary.

A regression could bypass an admin boundary or leave recovery state inconsistent.
"""

from __future__ import annotations

from ._admin_shared import (
    AuditLog,
    Session,
    Tag,
    TestClient,
    _headers,
    _user,
    install_audit_listeners,
    select,
    utcnow,
)


class TestRestoreResource:
    def test_restore_unknown_resource_404(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-o")
        resp = client.post("/api/v1/admin/bogus/1/restore", headers=_headers(admin))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "resource_not_found"

    def test_restore_unknown_id_404(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-p")
        resp = client.post("/api/v1/admin/tags/999/restore", headers=_headers(admin))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "resource_id_not_found"

    def test_restore_success(self, client: TestClient, db_session: Session) -> None:
        admin = _user(db_session, "admin-q")
        tag = Tag(name="restorable", slug="restorable", deleted_at=utcnow())
        db_session.add(tag)
        db_session.commit()
        db_session.refresh(tag)

        resp = client.post(
            f"/api/v1/admin/tags/{tag.id}/restore", headers=_headers(admin)
        )
        assert resp.status_code == 200
        assert resp.json() == {"restored": True}
        db_session.refresh(tag)
        assert tag.deleted_at is None


class TestAuditLog:
    def test_cookie_authenticated_mutation_records_actor(
        self, client: TestClient, db_session: Session
    ) -> None:
        install_audit_listeners()
        admin = _user(db_session, "cookie-audit-admin")
        login = client.post(
            "/api/v1/auth/login",
            json={"username": admin.username, "password": "Password123"},
        )
        assert login.status_code == 200

        created = client.post(
            "/api/v1/admin/users",
            json={"username": "cookie-audit-target", "password": "Password123"},
        )

        assert created.status_code == 201
        audit_row = db_session.exec(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "users",
                AuditLog.resource_id == created.json()["id"],
            )
            .order_by(AuditLog.id.desc())
        ).first()
        assert audit_row is not None
        assert audit_row.actor_id == admin.id

    def test_list_audit_returns_list(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Other admin actions in this suite auto-log to audit_logs on their own
        # committed sessions (not rolled back with db_session), so this can't
        # assert an empty list — only that the endpoint returns valid shape.
        admin = _user(db_session, "admin-r")
        resp = client.get("/api/v1/admin/audit", headers=_headers(admin))
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_audit_filters_by_resource_and_id(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "admin-s")
        db_session.add(
            AuditLog(
                actor_id=admin.id,
                action="update",
                resource_type="matrix_combined_filter",
                resource_id=76543210,
            )
        )
        db_session.add(
            AuditLog(
                actor_id=admin.id,
                action="update",
                resource_type="matrix_other_filter",
                resource_id=2,
            )
        )
        db_session.commit()

        resp = client.get(
            "/api/v1/admin/audit",
            params={
                "resource": "matrix_combined_filter",
                "resource_id": 76543210,
            },
            headers=_headers(admin),
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["resource_type"] == "matrix_combined_filter"


class TestRunGc:
    def test_run_gc(self, client: TestClient, db_session: Session) -> None:
        admin = _user(db_session, "admin-t")
        resp = client.post("/api/v1/admin/gc", headers=_headers(admin))
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)
