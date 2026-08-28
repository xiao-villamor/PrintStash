"""Share links: the only part of the vault an anonymous caller can read.

Everything under `/share/{token}` answers without credentials, so the token *is* the
authorisation and every handler has to re-derive what it grants. Three properties carry
the whole surface, and each one is a way the vault leaks if it regresses:

* **A token grants exactly one model.** File ids are guessable, so asking for another
  model's file through a valid token must 404 rather than serve it.
* **A token stops working when it should.** Revoked, expired, or never real — all 404,
  with no hint that the difference exists.
* **View-only means view-only.** A link created without download may render a mesh
  preview but must refuse the original bytes, G-code included.

The admin half is authenticated and goes through the model's collection RBAC: creating
or revoking a link is an EDIT-level act on the model, listing is VIEW-level.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    CollectionRole,
    File,
    FileRevisionStatus,
    Model,
    ShareLink,
    User,
)
from app.services.storage_backend import get_backend
from tests.factories import (
    build_collection,
    build_file,
    build_model,
    grant_collection_role,
)


def _make_model(
    db_session: Session, *, name="M", slug="m", hash_="h" * 64, **fields: Any
) -> Model:
    model = build_model(db_session, name=name, slug=slug, hash=hash_, **fields)
    db_session.refresh(model)
    return model


def _make_file(
    db_session: Session,
    model: Model,
    *,
    filename="part.stl",
    ftype="stl",
    path=None,
    version=1,
) -> File:
    row = build_file(
        db_session,
        model,
        path=path or f"/nonexistent/{filename}",
        filename=filename,
        file_type=ftype,
        version=version,
        size_bytes=10,
        sha256=f"{filename:a<64}"[:64],
    )
    return row


def _create_share(
    client: TestClient, headers: dict[str, str], model_id: int, **body: Any
) -> dict:
    payload = {"expires_in_days": 7, "allow_download": False, **body}
    response = client.post(
        f"/api/v1/models/{model_id}/shares", json=payload, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def stored_blob():
    """Write real bytes at a key so a handler can serve them."""

    def write(key: str, data: bytes = b"stl-bytes") -> str:
        get_backend().write_bytes(data, key)
        return key

    return write


class TestPublicModelView:
    def test_serves_the_shared_model_without_credentials(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, name="Shared", slug="shared", hash_="s" * 64)
        created = _create_share(client, auth_headers, model.id)

        response = client.get(f"/api/v1/share/{created['token']}")

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Shared"

    def test_does_not_reach_another_models_file(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        shared = _make_model(db_session, slug="shared", hash_="s" * 64)
        _make_file(db_session, shared, filename="shared.stl")
        other = _make_model(db_session, slug="other", hash_="o" * 64)
        other_file = _make_file(db_session, other, filename="secret.stl")
        created = _create_share(client, auth_headers, shared.id)

        response = client.get(
            f"/api/v1/share/{created['token']}/files/{other_file.id}/stl"
        )

        assert response.status_code == 404, "a token grants exactly one model"

    def test_rejects_a_token_that_was_never_real(self, client: TestClient) -> None:
        assert client.get("/api/v1/share/not-a-real-token").status_code == 404

    def test_rejects_a_revoked_token(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="rev", hash_="r" * 64)
        created = _create_share(client, auth_headers, model.id)
        client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers)

        assert client.get(f"/api/v1/share/{created['token']}").status_code == 404

    def test_rejects_an_expired_token(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="exp", hash_="e" * 64)
        created = _create_share(client, auth_headers, model.id)
        link = db_session.get(ShareLink, created["id"])
        link.expires_at = utcnow() - timedelta(days=1)
        db_session.add(link)
        db_session.commit()

        assert client.get(f"/api/v1/share/{created['token']}").status_code == 404

    def test_counts_the_access(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="counted", hash_="c" * 64)
        created = _create_share(client, auth_headers, model.id)

        client.get(f"/api/v1/share/{created['token']}")
        client.get(f"/api/v1/share/{created['token']}")

        db_session.expire_all()
        # The owner's share list shows how often a link has been opened.
        assert db_session.get(ShareLink, created["id"]).access_count == 2

    def test_serves_only_the_revisions_the_link_scopes_to(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="scope", hash_="q" * 64)
        mesh = _make_file(db_session, model, filename="part.stl", version=1)
        _make_file(db_session, model, filename="rev1.gcode", ftype="gcode", version=2)
        chosen = _make_file(
            db_session, model, filename="rev2.gcode", ftype="gcode", version=3
        )
        chosen.revision_label = "PLA fast"
        chosen.revision_status = FileRevisionStatus.KNOWN_GOOD
        db_session.add(chosen)
        db_session.commit()

        created = _create_share(
            client, auth_headers, model.id, revision_file_ids=[chosen.id]
        )
        body = client.get(f"/api/v1/share/{created['token']}").json()

        assert {row["id"] for row in body["files"]} == {mesh.id, chosen.id}

    def test_describes_the_shared_revision(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="described", hash_="q" * 64)
        _make_file(db_session, model, filename="part.stl", version=1)
        chosen = _make_file(
            db_session, model, filename="rev2.gcode", ftype="gcode", version=3
        )
        chosen.revision_label = "PLA fast"
        chosen.revision_status = FileRevisionStatus.KNOWN_GOOD
        db_session.add(chosen)
        db_session.commit()
        created = _create_share(
            client, auth_headers, model.id, revision_file_ids=[chosen.id]
        )

        body = client.get(f"/api/v1/share/{created['token']}").json()

        shared = next(row for row in body["files"] if row["id"] == chosen.id)
        assert shared["revision_label"] == "PLA fast"
        assert shared["revision_status"] == "known_good"

    def test_hides_a_revision_the_link_excludes(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="excluded", hash_="q" * 64)
        excluded = _make_file(
            db_session, model, filename="rev1.gcode", ftype="gcode", version=2
        )
        chosen = _make_file(
            db_session, model, filename="rev2.gcode", ftype="gcode", version=3
        )
        created = _create_share(
            client, auth_headers, model.id, revision_file_ids=[chosen.id]
        )

        response = client.get(
            f"/api/v1/share/{created['token']}/files/{excluded.id}/gcode"
        )

        assert response.status_code == 404


class TestSharedThumbnail:
    def test_serves_the_models_thumbnail(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="has-thumb", hash_="1a" * 32)
        mesh = _make_file(db_session, model, filename="x.stl", ftype="stl")
        backend = get_backend()
        backend.write_bytes(b"webp-bytes", backend.thumbnail_key(mesh.id))
        model.thumbnail_file_id = mesh.id
        db_session.add(model)
        db_session.commit()
        created = _create_share(client, auth_headers, model.id)

        response = client.get(f"/api/v1/share/{created['token']}/thumbnail")

        assert response.status_code == 200, response.text
        assert response.content == b"webp-bytes"

    def test_reports_a_model_without_a_thumbnail_as_not_found(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="no-thumb", hash_="1" * 64)
        created = _create_share(client, auth_headers, model.id)

        response = client.get(f"/api/v1/share/{created['token']}/thumbnail")

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "not_found"


class TestSharedStl:
    def test_serves_a_mesh_file(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        stored_blob,
    ) -> None:
        model = _make_model(db_session, slug="stl-ok", hash_="2a" * 32)
        key = stored_blob("share-stl-ok.stl", b"solid stl-bytes endsolid")
        mesh = _make_file(db_session, model, filename="part.stl", path=key)
        created = _create_share(client, auth_headers, model.id)

        response = client.get(f"/api/v1/share/{created['token']}/files/{mesh.id}/stl")

        assert response.status_code == 200, response.text

    def test_reports_a_non_mesh_file_as_not_found(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="stl-wrong-type", hash_="2" * 64)
        gcode = _make_file(db_session, model, filename="x.gcode", ftype="gcode")
        created = _create_share(client, auth_headers, model.id)

        response = client.get(f"/api/v1/share/{created['token']}/files/{gcode.id}/stl")

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "not_found"

    def test_serves_a_mesh_preview_on_a_view_only_link(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        stored_blob,
    ) -> None:
        model = _make_model(db_session, slug="stl-view-only", hash_="2b" * 32)
        key = stored_blob("share-stl-view-only.stl", b"solid s endsolid")
        mesh = _make_file(db_session, model, filename="part.stl", path=key)
        created = _create_share(client, auth_headers, model.id, allow_download=False)

        response = client.get(f"/api/v1/share/{created['token']}/files/{mesh.id}/stl")

        assert response.status_code == 200, "the viewer works without download rights"


class TestSharedDownload:
    def test_serves_the_original_file(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        stored_blob,
    ) -> None:
        model = _make_model(db_session, slug="dl-ok", hash_="5" * 64)
        key = stored_blob("share-download-ok.stl")
        row = _make_file(db_session, model, path=key)
        created = _create_share(client, auth_headers, model.id, allow_download=True)

        response = client.get(
            f"/api/v1/share/{created['token']}/files/{row.id}/download"
        )

        assert response.status_code == 200, response.text
        assert response.content == b"stl-bytes"

    def test_refuses_a_view_only_link(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="dl-disabled", hash_="3" * 64)
        row = _make_file(db_session, model)
        created = _create_share(client, auth_headers, model.id, allow_download=False)

        response = client.get(
            f"/api/v1/share/{created['token']}/files/{row.id}/download"
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "download_disabled"

    def test_reports_a_missing_blob_as_gone(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="dl-missing-blob", hash_="4" * 64)
        row = _make_file(db_session, model)
        created = _create_share(client, auth_headers, model.id, allow_download=True)

        response = client.get(
            f"/api/v1/share/{created['token']}/files/{row.id}/download"
        )

        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "file_blob_missing"


class TestSharedGcode:
    def test_serves_the_gcode(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        stored_blob,
    ) -> None:
        model = _make_model(db_session, slug="gcode-ok", hash_="8" * 64)
        key = stored_blob("share-gcode-ok.gcode", b"G1 X0 Y0\n")
        gcode = _make_file(
            db_session, model, filename="x.gcode", ftype="gcode", path=key
        )
        created = _create_share(client, auth_headers, model.id, allow_download=True)

        response = client.get(
            f"/api/v1/share/{created['token']}/files/{gcode.id}/gcode"
        )

        assert response.status_code == 200, response.text
        assert response.content == b"G1 X0 Y0\n"

    def test_refuses_a_view_only_link(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        stored_blob,
    ) -> None:
        model = _make_model(db_session, slug="gcode-view-only", hash_="5a" * 32)
        key = stored_blob("share-gcode-view-only.gcode", b"G1 X0 Y0\n")
        gcode = _make_file(
            db_session, model, filename="private.gcode", ftype="gcode", path=key
        )
        created = _create_share(client, auth_headers, model.id, allow_download=False)

        response = client.get(
            f"/api/v1/share/{created['token']}/files/{gcode.id}/gcode"
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "download_disabled"

    def test_reports_a_non_gcode_file_as_not_found(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="gcode-wrong-type", hash_="6" * 64)
        mesh = _make_file(db_session, model, filename="x.stl", ftype="stl")
        created = _create_share(client, auth_headers, model.id, allow_download=True)

        response = client.get(f"/api/v1/share/{created['token']}/files/{mesh.id}/gcode")

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "not_found"

    def test_reports_a_missing_blob_as_gone(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="gcode-missing-blob", hash_="7" * 64)
        gcode = _make_file(db_session, model, filename="x.gcode", ftype="gcode")
        created = _create_share(client, auth_headers, model.id, allow_download=True)

        response = client.get(
            f"/api/v1/share/{created['token']}/files/{gcode.id}/gcode"
        )

        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "file_blob_missing"


class TestCreateShare:
    def test_returns_a_share_token_with_its_url(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="creatable", hash_="b1" * 32)

        created = _create_share(client, auth_headers, model.id)

        assert created["token"]
        assert created["url"] == f"/share/{created['token']}"

    def test_reports_an_unknown_model_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/models/999/shares",
            json={"expires_in_days": 7, "allow_download": False},
            headers=auth_headers,
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "model_not_found"

    def test_reports_a_trashed_model_as_not_found(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(
            db_session, slug="trashed", hash_="b2" * 32, deleted_at=utcnow()
        )

        response = client.post(
            f"/api/v1/models/{model.id}/shares",
            json={"expires_in_days": 7, "allow_download": False},
            headers=auth_headers,
        )

        assert response.status_code == 404, "a trashed model is not shareable"

    def test_requires_edit_on_the_models_collection(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
    ) -> None:
        collection = build_collection(db_session, name="Team", slug="team", path="team")
        model = _make_model(
            db_session, slug="team-model", hash_="b3" * 32, collection_id=collection.id
        )
        viewer: User = make_user("viewer-only")
        grant_collection_role(db_session, viewer, collection, CollectionRole.VIEW)

        response = client.post(
            f"/api/v1/models/{model.id}/shares",
            json={"expires_in_days": 7, "allow_download": False},
            headers=headers_for(viewer),
        )

        assert response.status_code == 403, "publishing a model is an EDIT-level act"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, db_session: Session
    ) -> None:
        model = _make_model(db_session, slug="anon", hash_="b4" * 32)

        response = client.post(
            f"/api/v1/models/{model.id}/shares",
            json={"expires_in_days": 7, "allow_download": False},
        )

        assert response.status_code == 401


class TestListShares:
    def test_lists_a_models_links(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="listable", hash_="9" * 64)
        _create_share(client, auth_headers, model.id)

        response = client.get(f"/api/v1/models/{model.id}/shares", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert len(response.json()) == 1

    def test_never_returns_the_raw_token(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="tokenless", hash_="a2" * 32)
        created = _create_share(client, auth_headers, model.id)

        listed = client.get(
            f"/api/v1/models/{model.id}/shares", headers=auth_headers
        ).text

        assert created["token"] not in listed, (
            "the raw token is shown once, at creation"
        )

    def test_reports_an_unknown_model_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/models/999/shares", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "model_not_found"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, db_session: Session
    ) -> None:
        model = _make_model(db_session, slug="anon-list", hash_="a3" * 32)

        assert client.get(f"/api/v1/models/{model.id}/shares").status_code == 401


class TestRevokeShare:
    def test_marks_the_link_revoked(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="revocable", hash_="a1" * 32)
        created = _create_share(client, auth_headers, model.id)

        response = client.delete(
            f"/api/v1/shares/{created['id']}", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["revoked_at"] is not None

    def test_reports_an_unknown_link_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.delete("/api/v1/shares/999", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "share_not_found"

    def test_requires_edit_on_the_models_collection(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        make_user,
        headers_for,
    ) -> None:
        collection = build_collection(db_session, name="Team", slug="team", path="team")
        model = _make_model(
            db_session, slug="team-model", hash_="b5" * 32, collection_id=collection.id
        )
        created = _create_share(client, auth_headers, model.id)
        viewer: User = make_user("revoke-viewer")
        grant_collection_role(db_session, viewer, collection, CollectionRole.VIEW)

        response = client.delete(
            f"/api/v1/shares/{created['id']}", headers=headers_for(viewer)
        )

        assert response.status_code == 403, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _make_model(db_session, slug="anon-revoke", hash_="a4" * 32)
        created = _create_share(client, auth_headers, model.id)

        assert client.delete(f"/api/v1/shares/{created['id']}").status_code == 401
