"""Caption routes use the Subject's own VIEW/EDIT boundary for all four kinds."""

import pytest
from sqlmodel import select

from app.db.models import AuditLog, CollectionRole, SubjectCaption
from tests.factories import bearer


class TestCaptionAPI:
    @pytest.mark.parametrize(
        "payload",
        [
            {"action": "edit"},
            {"action": "edit", "text": " \n\t"},
            {"action": "dismiss", "text": "unexpected"},
            {"action": "reset", "text": "unexpected"},
            {"action": "generate", "text": "unexpected"},
        ],
    )
    def test_rejects_inconsistent_edits(
        self, client, db_session, make_model, auth_headers, payload
    ):
        model = make_model()
        response = client.patch(
            f"/api/v1/subjects/model/{model.id}/caption",
            headers=auth_headers,
            json=payload,
        )
        assert response.status_code == 422, response.text
        assert db_session.exec(select(SubjectCaption)).all() == []

    @pytest.mark.parametrize(
        "kind", ["model", "collection", "multipart_model", "document"]
    )
    def test_enforces_subject_edit_permissions(
        self,
        client,
        db_session,
        make_user,
        make_model,
        make_collection,
        make_document,
        make_multipart_model,
        grant_role,
        kind,
    ):
        parent = make_collection("Shared")
        owner = {
            "model": lambda: make_model(collection=parent),
            "collection": lambda: parent,
            "document": lambda: make_document(collection_id=parent.id),
            "multipart_model": lambda: make_multipart_model(collection=parent),
        }[kind]()
        viewer, editor, outsider = make_user(), make_user(), make_user()
        grant_role(viewer, parent, CollectionRole.VIEW)
        grant_role(editor, parent, CollectionRole.EDIT)
        url = f"/api/v1/subjects/{kind}/{owner.id}/caption"
        viewed = client.get(url, headers=bearer(viewer))
        assert viewed.status_code == 200
        assert not viewed.json()["can_edit"]
        assert (
            client.patch(
                url,
                headers=bearer(viewer),
                json={"action": "edit", "text": "Unauthorized"},
            ).status_code
            == 403
        )
        assert client.get(url, headers=bearer(outsider)).status_code == 404
        assert (
            client.patch(
                url, headers=bearer(outsider), json={"action": "dismiss"}
            ).status_code
            == 404
        )
        updated = client.patch(
            url,
            headers=bearer(editor),
            json={"action": "edit", "text": "<img src=x onerror=alert(1)>"},
        )
        assert updated.status_code == 200
        assert updated.json()["state"] == "edited"
        assert updated.json()["text"] == "<img src=x onerror=alert(1)>"
        assert updated.json()["edited_by"] == editor.id
        assert (
            client.patch(
                url, headers=bearer(editor), json={"action": "edit", "text": "x" * 2049}
            ).status_code
            == 422
        )
        dismissed = client.patch(
            url,
            headers=bearer(editor),
            json={
                "action": "dismiss",
                "version_token": updated.json()["version_token"],
            },
        )
        assert dismissed.status_code == 200
        assert dismissed.json()["text"] == ""
        assert db_session.exec(select(SubjectCaption)).one().state == "dismissed"
        audit = db_session.exec(
            select(AuditLog).where(AuditLog.action == "subject_caption_edit")
        ).one()
        assert audit.actor_id == editor.id
        assert "onerror" not in audit.diff_json

    def test_requires_a_signed_in_principal(self, client, make_model):
        model = make_model()
        url = f"/api/v1/subjects/model/{model.id}/caption"
        assert client.get(url).status_code == 401
        assert client.patch(url, json={"action": "dismiss"}).status_code == 401
