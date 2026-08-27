"""API tests for /share (public) and admin share management endpoints,
targeting error branches not exercised by
tests/integration/api/v1/models/test_share.py's
TestShareIsolation (missing_blob, no-thumbnail, download-disabled,
gcode wrong-type, model-not-found on admin routes)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import ensure_utc, utcnow
from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    File,
    Model,
    ShareLink,
    User,
)
from app.services.auth import create_access_token, hash_password


def _make_model(db_session: Session, *, name="M", slug="m", hash_="h" * 64) -> Model:
    m = Model(name=name, slug=slug, hash=hash_)
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    return m


def _make_file(
    db_session: Session, model: Model, *, filename="part.stl", ftype="stl", path=None
) -> File:
    f = File(
        model_id=model.id,
        path=path or f"/nonexistent/{filename}",
        original_filename=filename,
        file_type=ftype,
        version=1,
        size_bytes=10,
        sha256="a" * 64,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)
    return f


def _create_share(client: TestClient, auth_headers, model_id: int, **body) -> dict:
    payload = {"expires_in_days": 7, "allow_download": False, **body}
    res = client.post(
        f"/api/v1/models/{model_id}/shares", json=payload, headers=auth_headers
    )
    assert res.status_code == 200, res.text
    return res.json()


def _assert_persisted_timestamp_in_window(
    value,
    *,
    request_started,
    request_finished,
    offset: timedelta,
) -> None:
    """Bound persisted time by the request window at SQLite's microsecond precision."""
    db_precision = timedelta(microseconds=1)
    persisted = ensure_utc(value)
    assert request_started + offset - db_precision <= persisted
    assert persisted <= request_finished + offset + db_precision


def _regular_user_headers(
    db_session: Session, username: str
) -> tuple[User, dict[str, str]]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    return user, {"Authorization": f"Bearer {token}"}


def _collection_model(db_session: Session, *, suffix: str) -> tuple[Collection, Model]:
    collection = Collection(
        name=f"Share {suffix}", slug=f"share-{suffix}", path=f"share-{suffix}"
    )
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    model = Model(
        name=f"Model {suffix}",
        slug=f"model-{suffix}",
        hash=(suffix[0] * 64),
        collection_id=collection.id,
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    return collection, model


def _grant_collection(
    db_session: Session,
    user: User,
    collection: Collection,
    role: CollectionRole,
) -> None:
    db_session.add(
        CollectionPermission(user_id=user.id, collection_id=collection.id, role=role)
    )
    db_session.commit()


class TestSharedThumbnail:
    def test_no_thumbnail_404(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="no-thumb", hash_="1" * 64)
        created = _create_share(client, auth_headers, model.id)
        resp = client.get(f"/api/v1/share/{created['token']}/thumbnail")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    def test_thumbnail_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        from app.services.storage_backend import get_backend

        model = _make_model(db_session, slug="has-thumb", hash_="1a" * 32)
        gcode_file = _make_file(db_session, model, filename="x.stl", ftype="stl")
        backend = get_backend()
        backend.write_bytes(b"webp-bytes", backend.thumbnail_key(gcode_file.id))
        model.thumbnail_file_id = gcode_file.id
        db_session.add(model)
        db_session.commit()

        created = _create_share(client, auth_headers, model.id)
        resp = client.get(f"/api/v1/share/{created['token']}/thumbnail")
        assert resp.status_code == 200
        assert resp.content == b"webp-bytes"


class TestSharedStl:
    def test_non_mesh_file_type_404(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="stl-wrong-type", hash_="2" * 64)
        gcode_file = _make_file(db_session, model, filename="x.gcode", ftype="gcode")
        created = _create_share(client, auth_headers, model.id)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{gcode_file.id}/stl")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    def test_mesh_file_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        from app.services.storage_backend import get_backend

        model = _make_model(db_session, slug="stl-ok", hash_="2a" * 32)
        key = "share-stl-ok.stl"
        get_backend().write_bytes(b"solid stl-bytes endsolid", key)
        stl_file = _make_file(
            db_session, model, filename="part.stl", ftype="stl", path=key
        )
        created = _create_share(client, auth_headers, model.id)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{stl_file.id}/stl")
        assert resp.status_code == 200


class TestSharedDownload:
    def test_download_disabled_forbidden(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="dl-disabled", hash_="3" * 64)
        f = _make_file(db_session, model)
        created = _create_share(client, auth_headers, model.id, allow_download=False)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{f.id}/download")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "download_disabled"

    def test_download_missing_blob_410(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="dl-missing-blob", hash_="4" * 64)
        f = _make_file(db_session, model)
        created = _create_share(client, auth_headers, model.id, allow_download=True)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{f.id}/download")
        assert resp.status_code == 410
        assert resp.json()["detail"] == "file_blob_missing"

    def test_download_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        from app.services.storage_backend import get_backend

        model = _make_model(db_session, slug="dl-ok", hash_="5" * 64)
        key = "share-download-ok.stl"
        get_backend().write_bytes(b"stl-bytes", key)
        f = _make_file(db_session, model, path=key)
        created = _create_share(client, auth_headers, model.id, allow_download=True)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{f.id}/download")
        assert resp.status_code == 200
        assert resp.content == b"stl-bytes"


class TestSharedGcode:
    def test_view_only_share_cannot_fetch_original_gcode(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        from app.services.storage_backend import get_backend

        model = _make_model(db_session, slug="gcode-view-only", hash_="5a" * 32)
        key = "share-gcode-view-only.gcode"
        get_backend().write_bytes(b"G1 X0 Y0\n", key)
        gcode_file = _make_file(
            db_session,
            model,
            filename="private.gcode",
            ftype="gcode",
            path=key,
        )
        created = _create_share(client, auth_headers, model.id, allow_download=False)

        resp = client.get(
            f"/api/v1/share/{created['token']}/files/{gcode_file.id}/gcode"
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "download_disabled"

    def test_wrong_file_type_404(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="gcode-wrong-type", hash_="6" * 64)
        stl_file = _make_file(db_session, model, filename="x.stl", ftype="stl")
        created = _create_share(client, auth_headers, model.id, allow_download=True)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{stl_file.id}/gcode")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    def test_missing_blob_410(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="gcode-missing-blob", hash_="7" * 64)
        gcode_file = _make_file(db_session, model, filename="x.gcode", ftype="gcode")
        created = _create_share(client, auth_headers, model.id, allow_download=True)
        resp = client.get(
            f"/api/v1/share/{created['token']}/files/{gcode_file.id}/gcode"
        )
        assert resp.status_code == 410
        assert resp.json()["detail"] == "file_blob_missing"

    def test_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        from app.services.storage_backend import get_backend

        model = _make_model(db_session, slug="gcode-ok", hash_="8" * 64)
        key = "share-gcode-ok.gcode"
        get_backend().write_bytes(b"G1 X0 Y0\n", key)
        gcode_file = _make_file(
            db_session, model, filename="x.gcode", ftype="gcode", path=key
        )
        created = _create_share(client, auth_headers, model.id, allow_download=True)
        resp = client.get(
            f"/api/v1/share/{created['token']}/files/{gcode_file.id}/gcode"
        )
        assert resp.status_code == 200
        assert resp.content == b"G1 X0 Y0\n"


class TestAdminShareManagement:
    def test_create_share_model_not_found(
        self, client: TestClient, auth_headers
    ) -> None:
        resp = client.post(
            "/api/v1/models/999/shares",
            json={"expires_in_days": 7, "allow_download": False},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "model_not_found"

    def test_list_shares_model_not_found(
        self, client: TestClient, auth_headers
    ) -> None:
        resp = client.get("/api/v1/models/999/shares", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "model_not_found"

    def test_list_shares_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="listable", hash_="9" * 64)
        _create_share(client, auth_headers, model.id)
        resp = client.get(f"/api/v1/models/{model.id}/shares", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_revoke_share_not_found(self, client: TestClient, auth_headers) -> None:
        resp = client.delete("/api/v1/shares/999", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "share_not_found"

    def test_revoke_share_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="revocable", hash_="a1" * 32)
        created = _create_share(client, auth_headers, model.id)
        resp = client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["revoked_at"] is not None


class TestCreateModelShareBoundaries:
    def test_defaults_expiry_to_seven_days(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="default-share", hash_="b" * 64)

        request_started = utcnow()
        response = client.post(
            f"/api/v1/models/{model.id}/shares", json={}, headers=auth_headers
        )
        request_finished = utcnow()

        assert response.status_code == 200, response.text
        link = db_session.get(ShareLink, response.json()["id"])
        assert link is not None
        _assert_persisted_timestamp_in_window(
            link.expires_at,
            request_started=request_started,
            request_finished=request_finished,
            offset=timedelta(days=7),
        )

    def test_defaults_downloads_to_disabled(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="default-download-share", hash_="b1" * 32)

        response = client.post(
            f"/api/v1/models/{model.id}/shares", json={}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        link = db_session.get(ShareLink, response.json()["id"])
        assert link is not None
        assert link.allow_download is False

    def test_stores_only_a_hash_of_the_one_time_token(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="hashed-share", hash_="c" * 64)

        response = client.post(
            f"/api/v1/models/{model.id}/shares", json={}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        link = db_session.get(ShareLink, response.json()["id"])
        assert link is not None
        assert response.json()["token"] not in link.token_hash
        assert len(link.token_hash) == 64

    @pytest.mark.parametrize(
        "expires_in_days",
        [pytest.param(0, id="below-min"), pytest.param(999, id="above-max")],
    )
    def test_clamps_expiry_to_the_supported_range(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        expires_in_days: int,
    ) -> None:
        model = _make_model(
            db_session,
            slug=f"clamped-{expires_in_days}",
            hash_=("d" if expires_in_days == 0 else "e") * 64,
        )

        request_started = utcnow()
        response = client.post(
            f"/api/v1/models/{model.id}/shares",
            json={"expires_in_days": expires_in_days},
            headers=auth_headers,
        )
        request_finished = utcnow()

        assert response.status_code == 200, response.text
        link = db_session.get(ShareLink, response.json()["id"])
        assert link is not None
        expected_days = 1 if expires_in_days == 0 else 365
        _assert_persisted_timestamp_in_window(
            link.expires_at,
            request_started=request_started,
            request_finished=request_finished,
            offset=timedelta(days=expected_days),
        )

    def test_rejects_an_empty_revision_selection(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="empty-revisions", hash_="f" * 64)

        response = client.post(
            f"/api/v1/models/{model.id}/shares",
            json={"revision_file_ids": []},
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "no_revisions_selected"
        assert (
            db_session.exec(
                select(ShareLink).where(ShareLink.model_id == model.id)
            ).all()
            == []
        )

    def test_rejects_a_revision_from_a_foreign_model(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="selected-foreign", hash_="1" * 64)
        owner = _make_model(db_session, slug="foreign-owner", hash_="2" * 64)
        revision = _make_file(
            db_session, owner, filename="foreign.gcode", ftype="gcode"
        )

        response = client.post(
            f"/api/v1/models/{model.id}/shares",
            json={"revision_file_ids": [revision.id]},
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "invalid_revision_file_id"

    def test_rejects_a_trashed_revision(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="selected-trashed", hash_="3" * 64)
        revision = _make_file(
            db_session, model, filename="trashed.gcode", ftype="gcode"
        )
        revision.deleted_at = utcnow()
        db_session.add(revision)
        db_session.commit()

        response = client.post(
            f"/api/v1/models/{model.id}/shares",
            json={"revision_file_ids": [revision.id]},
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "invalid_revision_file_id"

    def test_denies_share_creation_to_a_collection_viewer(
        self, client: TestClient, db_session: Session
    ) -> None:
        collection, model = _collection_model(db_session, suffix="view-denied")
        user, headers = _regular_user_headers(db_session, "share-viewer")
        _grant_collection(db_session, user, collection, CollectionRole.VIEW)

        response = client.post(
            f"/api/v1/models/{model.id}/shares", json={}, headers=headers
        )

        assert response.status_code == 403
        assert (
            db_session.exec(
                select(ShareLink).where(ShareLink.model_id == model.id)
            ).all()
            == []
        )

    def test_hides_a_trashed_model_from_share_creation(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="trashed-create", hash_="3" * 64)
        model.deleted_at = utcnow()
        db_session.add(model)
        db_session.commit()

        response = client.post(
            f"/api/v1/models/{model.id}/shares", json={}, headers=auth_headers
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "model_not_found"


class TestListModelSharesBoundaries:
    def test_returns_an_empty_list_without_links(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="empty-share-list", hash_="4" * 64)

        response = client.get(f"/api/v1/models/{model.id}/shares", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json() == []

    def test_never_returns_raw_tokens_in_the_management_list(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="tokenless-list", hash_="5" * 64)
        created = _create_share(client, auth_headers, model.id)

        response = client.get(f"/api/v1/models/{model.id}/shares", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert "token" not in response.json()[0]
        assert created["token"] not in response.text

    def test_denies_listing_an_inaccessible_collection_model(
        self, client: TestClient, db_session: Session
    ) -> None:
        _, model = _collection_model(db_session, suffix="list-denied")
        _, headers = _regular_user_headers(db_session, "share-list-denied")

        response = client.get(f"/api/v1/models/{model.id}/shares", headers=headers)

        assert response.status_code == 403


class TestRevokeModelShareBoundaries:
    def test_keeps_an_already_revoked_link_inactive(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="idempotent-revoke", hash_="6" * 64)
        created = _create_share(client, auth_headers, model.id)
        first = client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers)

        second = client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["is_active"] is False

    def test_denies_revoking_a_share_for_an_inaccessible_model(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        _, model = _collection_model(db_session, suffix="revoke-denied")
        created = _create_share(client, auth_headers, model.id)
        _, denied_headers = _regular_user_headers(db_session, "share-revoke-denied")

        response = client.delete(
            f"/api/v1/shares/{created['id']}", headers=denied_headers
        )

        assert response.status_code == 403
        link = db_session.get(ShareLink, created["id"])
        assert link is not None
        assert link.revoked_at is None
