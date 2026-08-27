"""Generic admin deletion and restoration remain superuser-only."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import Tag

from ._admin_shared import _headers, _user


class TestAdminDeleteResource:
    def test_denies_a_non_superuser_from_deleting_resources(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = _user(db_session, "regular-resource-deleter", superuser=False)
        tag = Tag(name="delete-protected", slug="delete-protected")
        db_session.add(tag)
        db_session.commit()
        db_session.refresh(tag)

        response = client.delete(
            f"/api/v1/admin/tags/{tag.id}", headers=_headers(caller)
        )

        assert response.status_code == 403, response.text
        db_session.refresh(tag)
        assert tag.deleted_at is None


class TestRestoreResource:
    def test_denies_a_non_superuser_from_restoring_resources(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = _user(db_session, "regular-resource-restorer", superuser=False)
        tag = Tag(
            name="restore-protected",
            slug="restore-protected",
            deleted_at=utcnow(),
        )
        db_session.add(tag)
        db_session.commit()
        db_session.refresh(tag)

        response = client.post(
            f"/api/v1/admin/tags/{tag.id}/restore", headers=_headers(caller)
        )

        assert response.status_code == 403, response.text
        db_session.refresh(tag)
        assert tag.deleted_at is not None
