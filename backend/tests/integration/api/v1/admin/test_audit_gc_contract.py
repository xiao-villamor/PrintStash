"""Admin audit and retention endpoints expose safe, bounded recovery results."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import AuditLog, Tag

from ._admin_shared import _headers, _user


def _audit(
    session: Session,
    *,
    actor_id: int,
    resource_type: str,
    resource_id: int,
) -> AuditLog:
    row = AuditLog(
        actor_id=actor_id,
        action="update",
        resource_type=resource_type,
        resource_id=resource_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


class TestListAudit:
    def test_lists_audit_records(self, client: TestClient, db_session: Session) -> None:
        admin = _user(db_session, "audit-list-admin")
        row = _audit(
            db_session,
            actor_id=admin.id,
            resource_type="model",
            resource_id=17,
        )

        response = client.get("/api/v1/admin/audit", headers=_headers(admin))

        assert response.status_code == 200, response.text
        assert response.json()[0]["id"] == row.id
        assert response.json()[0]["actor_id"] == admin.id
        assert response.json()[0]["resource_type"] == "model"

    def test_filters_audit_records_by_resource(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "audit-resource-admin")
        wanted = _audit(
            db_session,
            actor_id=admin.id,
            resource_type="matrix_admin_resource_filter",
            resource_id=1,
        )
        _audit(
            db_session,
            actor_id=admin.id,
            resource_type="matrix_admin_other_resource",
            resource_id=1,
        )

        response = client.get(
            "/api/v1/admin/audit",
            params={"resource": "matrix_admin_resource_filter"},
            headers=_headers(admin),
        )

        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()] == [wanted.id]

    def test_filters_audit_records_by_resource_id(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "audit-id-admin")
        wanted = _audit(
            db_session,
            actor_id=admin.id,
            resource_type="model",
            resource_id=87654321,
        )
        _audit(
            db_session,
            actor_id=admin.id,
            resource_type="model",
            resource_id=87654322,
        )

        response = client.get(
            "/api/v1/admin/audit",
            params={"resource_id": 87654321},
            headers=_headers(admin),
        )

        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()] == [wanted.id]

    def test_paginates_audit_records(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "audit-page-admin")
        first = _audit(
            db_session,
            actor_id=admin.id,
            resource_type="matrix_admin_pagination",
            resource_id=1,
        )
        second = _audit(
            db_session,
            actor_id=admin.id,
            resource_type="matrix_admin_pagination",
            resource_id=2,
        )
        third = _audit(
            db_session,
            actor_id=admin.id,
            resource_type="matrix_admin_pagination",
            resource_id=3,
        )

        response = client.get(
            "/api/v1/admin/audit",
            params={
                "resource": "matrix_admin_pagination",
                "limit": 1,
                "offset": 1,
            },
            headers=_headers(admin),
        )

        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()] == [second.id]
        assert first.id != second.id != third.id

    @pytest.mark.parametrize(
        ("parameter", "value"),
        [
            pytest.param("limit", 0, id="zero-limit"),
            pytest.param("limit", 501, id="over-limit"),
            pytest.param("offset", -1, id="negative-offset"),
        ],
    )
    def test_validates_audit_pagination_boundaries(
        self,
        client: TestClient,
        db_session: Session,
        parameter: str,
        value: int,
    ) -> None:
        admin = _user(db_session, f"audit-validation-{parameter}-{value}")

        response = client.get(
            "/api/v1/admin/audit",
            params={parameter: value},
            headers=_headers(admin),
        )

        assert response.status_code == 422, response.text

    def test_denies_a_non_superuser_from_reading_audit_records(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = _user(db_session, "regular-audit-reader", superuser=False)

        response = client.get("/api/v1/admin/audit", headers=_headers(caller))

        assert response.status_code == 403, response.text


class TestRunGc:
    def test_runs_manual_trash_garbage_collection(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "gc-admin")
        _overlay["trash_retention_days"] = 30
        expired = Tag(
            name="expired",
            slug="expired",
            deleted_at=utcnow() - timedelta(days=31),
        )
        db_session.add(expired)
        db_session.commit()
        expired_id = expired.id

        response = client.post("/api/v1/admin/gc", headers=_headers(admin))

        assert response.status_code == 200, response.text
        assert response.json()["rows"] >= 1
        db_session.expire_all()
        assert db_session.get(Tag, expired_id) is None

    def test_leaves_unexpired_trash_during_manual_garbage_collection(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "gc-retention-admin")
        _overlay["trash_retention_days"] = 30
        recent = Tag(
            name="recent",
            slug="recent",
            deleted_at=utcnow() - timedelta(days=29),
        )
        db_session.add(recent)
        db_session.commit()

        response = client.post("/api/v1/admin/gc", headers=_headers(admin))

        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert db_session.exec(select(Tag).where(Tag.id == recent.id)).one()

    def test_denies_a_non_superuser_from_running_garbage_collection(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = _user(db_session, "regular-gc-caller", superuser=False)
        expired = Tag(
            name="protected-expired",
            slug="protected-expired",
            deleted_at=utcnow() - timedelta(days=999),
        )
        db_session.add(expired)
        db_session.commit()

        response = client.post("/api/v1/admin/gc", headers=_headers(caller))

        assert response.status_code == 403, response.text
        db_session.expire_all()
        assert db_session.get(Tag, expired.id) is not None
