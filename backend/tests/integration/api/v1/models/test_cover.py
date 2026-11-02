"""A Model's chosen cover survives derivation and has independent owned bytes.

Cover writes use the Model editing base, source selections snapshot the image,
and reset exposes the current automatic thumbnail without changing Artifacts.
"""

import pytest

from app.db.models import CollectionRole
from app.modules.media.thumbnail_publication import point_at
from tests.factories.content import png


@pytest.fixture
def model(make_model):
    return make_model("Cover controls")


@pytest.fixture
def upload(client, auth_headers, db_session, model):
    def put(data=None, headers=None):
        db_session.rollback()
        return client.put(
            f"/api/v1/models/{model.id}/cover",
            headers=auth_headers if headers is None else headers,
            files={"file": ("cover.png", png() if data is None else data, "image/png")},
        )

    return put


class TestModelCover:
    def test_preserves_the_chosen_cover_during_regeneration(
        self, client, auth_headers, db_session, model, make_file, upload
    ):
        artifact = make_file(model)
        response = upload()
        assert response.status_code == 200, response.text
        chosen = response.json()["thumbnail_url"]
        before = client.get(chosen, headers=auth_headers).content

        point_at(db_session, artifact, "regenerated.webp")
        db_session.commit()
        result = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)

        assert result.json()["thumbnail_url"] == chosen
        assert client.get(chosen, headers=auth_headers).content == before

    def test_reset_exposes_the_latest_automatic_thumbnail(
        self, client, auth_headers, db_session, model, make_file, upload
    ):
        artifact = make_file(model)
        response = upload()
        assert response.status_code == 200, response.text
        point_at(db_session, artifact, "regenerated.webp")
        db_session.commit()

        result = client.delete(f"/api/v1/models/{model.id}/cover", headers=auth_headers)

        assert result.status_code == 200, result.text
        assert result.json()["has_custom_cover"] is False
        assert (
            result.json()["thumbnail_url"] == f"/api/v1/files/{artifact.id}/thumbnail"
        )

    def test_source_selection_survives_source_cover_removal(
        self, client, auth_headers, db_session, model, make_provenance_source
    ):
        source = make_provenance_source(model)
        source_url = f"/api/v1/models/{model.id}/provenance/{source.id}/cover"
        db_session.rollback()
        source_upload = client.put(
            source_url,
            headers=auth_headers,
            files={"file": ("source.png", png(), "image/png")},
        )
        assert source_upload.status_code == 200, source_upload.text
        original = client.get(source_url + "/content", headers=auth_headers).content

        selected = client.post(
            f"/api/v1/models/{model.id}/cover/source",
            headers=auth_headers,
            json={"source_id": source.id},
        )
        assert selected.status_code == 200, selected.text
        removed = client.delete(source_url, headers=auth_headers)
        assert removed.status_code == 204, removed.text

        assert (
            client.get(selected.json()["thumbnail_url"], headers=auth_headers).content
            == original
        )

    def test_returns_a_conditional_edit_receipt(
        self, client, auth_headers, model, upload
    ):
        before = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        result = upload(
            headers={
                **auth_headers,
                "X-PrintStash-Edit-Contract": "conditional-v1",
                "If-Match": before.headers["etag"],
            }
        )

        assert result.status_code == 200, result.text
        assert result.json()["has_custom_cover"] is True
        assert result.json()["edit_version"] > before.json()["edit_version"]
        assert result.headers["etag"] != before.headers["etag"]
        image = client.get(result.json()["thumbnail_url"], headers=auth_headers)
        assert image.headers["content-type"] == "image/webp"
        assert image.headers["cache-control"] == "private, no-cache"
        assert image.content.startswith(b"RIFF")

    @pytest.mark.parametrize("method", ["put", "delete"])
    def test_rejects_stale_cover_edits(
        self, method, client, auth_headers, model, upload
    ):
        before = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        winner = upload()
        assert winner.status_code == 200
        headers = {
            **auth_headers,
            "X-PrintStash-Edit-Contract": "conditional-v1",
            "If-Match": before.headers["etag"],
        }
        if method == "put":
            result = upload(headers=headers)
        else:
            result = client.delete(f"/api/v1/models/{model.id}/cover", headers=headers)
        assert result.status_code == 412, result.text
        current = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        assert current.json()["thumbnail_url"] == winner.json()["thumbnail_url"]
        assert current.json()["edit_version"] == winner.json()["edit_version"]

    def test_requires_an_opted_in_precondition(self, auth_headers, upload):
        result = upload(
            headers={**auth_headers, "X-PrintStash-Edit-Contract": "conditional-v1"}
        )
        assert result.status_code == 428, result.text

    @pytest.mark.parametrize(
        "data",
        [b"", b"not an image", b"x" * (15 * 1024 * 1024 + 1)],
        ids=["empty", "corrupt", "oversize"],
    )
    def test_invalid_upload_preserves_the_chosen_cover(
        self, data, client, auth_headers, model, upload
    ):
        before = upload()
        result = upload(data=data)
        assert result.status_code == 422, result.text
        current = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        assert current.json()["thumbnail_url"] == before.json()["thumbnail_url"]
        assert current.json()["edit_version"] == before.json()["edit_version"]

    def test_reset_without_an_automatic_thumbnail_is_repeatable(
        self, client, auth_headers, model, upload
    ):
        assert upload().status_code == 200
        for _ in range(2):
            result = client.delete(
                f"/api/v1/models/{model.id}/cover", headers=auth_headers
            )
            assert result.status_code == 200, result.text
            assert result.json()["has_custom_cover"] is False
            assert result.json()["thumbnail_url"] is None

    @pytest.mark.parametrize("method", ["put", "post", "delete", "get"])
    def test_requires_authentication(self, method, client, model, upload):
        if method == "put":
            result = upload(headers={})
        elif method == "post":
            result = client.post(
                f"/api/v1/models/{model.id}/cover/source", json={"source_id": 1}
            )
        elif method == "delete":
            result = client.delete(f"/api/v1/models/{model.id}/cover")
        else:
            result = client.get(f"/api/v1/models/{model.id}/cover/content")
        assert result.status_code == 401

    def test_viewers_can_read_but_cannot_change_the_cover(
        self,
        client,
        auth_headers,
        db_session,
        make_collection,
        make_user,
        make_model,
        grant_role,
        headers_for,
    ):
        collection = make_collection("Shared covers")
        model = make_model(collection_id=collection.id)
        viewer = make_user()
        grant_role(viewer, collection, CollectionRole.VIEW)
        headers = headers_for(viewer)
        db_session.rollback()
        uploaded = client.put(
            f"/api/v1/models/{model.id}/cover",
            headers=auth_headers,
            files={"file": ("cover.png", png(), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        assert (
            client.get(uploaded.json()["thumbnail_url"], headers=headers).status_code
            == 200
        )
        assert (
            client.delete(
                f"/api/v1/models/{model.id}/cover", headers=headers
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/api/v1/models/{model.id}/cover",
                headers=headers,
                files={"file": ("cover.png", png(), "image/png")},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/models/{model.id}/cover/source",
                headers=headers,
                json={"source_id": 1},
            ).status_code
            == 403
        )

    def test_read_scope_cannot_upload(self, upload, user_headers):
        response = upload(headers=user_headers(is_superuser=True, scope="read"))
        assert response.status_code == 401
        assert response.json()["detail"] == "insufficient_scope"

    def test_trashed_model_cover_is_hidden(self, client, auth_headers, model, upload):
        saved = upload()
        assert saved.status_code == 200
        assert (
            client.delete(
                f"/api/v1/models/{model.id}", headers=auth_headers
            ).status_code
            == 204
        )

        assert (
            client.get(saved.json()["thumbnail_url"], headers=auth_headers).status_code
            == 404
        )
        assert upload().status_code == 404

    def test_source_must_belong_to_the_model(
        self,
        client,
        auth_headers,
        db_session,
        model,
        make_model,
        make_provenance_source,
    ):
        source = make_provenance_source(make_model())
        db_session.rollback()
        source_url = f"/api/v1/models/{source.model_id}/provenance/{source.id}/cover"
        assert (
            client.put(
                source_url,
                headers=auth_headers,
                files={"file": ("cover.png", png(), "image/png")},
            ).status_code
            == 200
        )
        result = client.post(
            f"/api/v1/models/{model.id}/cover/source",
            headers=auth_headers,
            json={"source_id": source.id},
        )
        assert result.status_code == 404, result.text
        assert (
            client.get(f"/api/v1/models/{model.id}", headers=auth_headers).json()[
                "has_custom_cover"
            ]
            is False
        )

    def test_missing_cover_reports_not_found(self, client, auth_headers, model):
        assert (
            client.get(
                f"/api/v1/models/{model.id}/cover/content", headers=auth_headers
            ).status_code
            == 404
        )

    def test_missing_stored_cover_reports_gone(
        self, client, auth_headers, db_session, model, make_model_cover
    ):
        make_model_cover(model)
        db_session.rollback()
        assert (
            client.get(
                f"/api/v1/models/{model.id}/cover/content", headers=auth_headers
            ).status_code
            == 410
        )

    def test_shared_thumbnail_uses_the_chosen_cover(
        self, client, auth_headers, model, upload
    ):
        saved = upload()
        expected = client.get(
            saved.json()["thumbnail_url"], headers=auth_headers
        ).content
        shared = client.post(
            f"/api/v1/models/{model.id}/shares",
            headers=auth_headers,
            json={"expires_in_days": 7, "allow_download": False},
        )
        assert shared.status_code == 200, shared.text
        result = client.get(f"/api/v1/share/{shared.json()['token']}/thumbnail")
        assert result.status_code == 200, result.text
        assert result.content == expected

    def test_replacement_retires_the_previous_bytes(self, db_session, model, upload):
        from app.db.models import ModelCover
        from app.modules.storage.storage_backend.runtime import get_backend

        assert upload().status_code == 200
        before = db_session.get(ModelCover, model.id)
        backend = get_backend()
        old_key = backend.model_cover_key(model.id, before.filename)
        assert backend.exists(old_key)
        db_session.rollback()

        assert upload(data=png(width=16)).status_code == 200

        assert not backend.exists(old_key)
        db_session.expire_all()
        after = db_session.get(ModelCover, model.id)
        assert backend.exists(backend.model_cover_key(model.id, after.filename))

    def test_backup_census_treats_the_cover_as_primary_data(
        self, db_session, model, upload
    ):
        from app.modules.storage.storage_utils import ownership_snapshot

        assert upload().status_code == 200

        owned = ownership_snapshot(db_session, discover=False)
        covers = [blob for blob in owned.primary if blob.resource_type == "model_cover"]
        assert len(covers) == 1
        assert covers[0].resource_id == model.id
        assert covers[0].expected_size > 0
        assert len(covers[0].expected_sha256) == 64

    def test_purge_removes_the_chosen_cover(self, db_session, model, upload):
        from app.db.models import ModelCover
        from app.modules.library.trash import hard_delete_model
        from app.modules.storage.storage_backend.runtime import get_backend
        from app.modules.storage.storage_deletion import process_storage_delete_intents

        assert upload().status_code == 200
        db_session.refresh(model)
        cover = db_session.get(ModelCover, model.id)
        model_id = model.id
        backend = get_backend()
        key = backend.model_cover_key(model_id, cover.filename)
        hard_delete_model(db_session, model)
        db_session.commit()
        process_storage_delete_intents()
        db_session.expire_all()

        assert db_session.get(ModelCover, model_id) is None
        assert not backend.exists(key)

    def test_publication_failure_keeps_the_previous_cover(
        self, client, auth_headers, model, upload, monkeypatch
    ):
        from app.modules.library import model_covers

        before = upload()

        def fail(*args, **kwargs):
            raise RuntimeError("publication failed")

        monkeypatch.setattr(model_covers, "finish_publication_batch", fail)

        with pytest.raises(RuntimeError, match="publication failed"):
            upload(data=png(width=16))

        current = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        assert current.json()["thumbnail_url"] == before.json()["thumbnail_url"]
        assert current.json()["edit_version"] == before.json()["edit_version"]
        assert (
            client.get(
                current.json()["thumbnail_url"], headers=auth_headers
            ).status_code
            == 200
        )

    def test_browse_thumbnail_uses_the_chosen_cover(
        self, client, auth_headers, model, upload
    ):
        saved = upload()
        response = client.get(
            "/api/v1/models/browse/thumbnails",
            params={"model_id": model.id},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert (
            response.json()["items"][0]["thumbnail_url"]
            == saved.json()["thumbnail_url"]
        )

    def test_source_selection_rejects_a_stale_editing_base(
        self, client, auth_headers, db_session, model, upload, make_provenance_source
    ):
        source = make_provenance_source(model)
        db_session.rollback()
        source_path = f"/api/v1/models/{model.id}/provenance/{source.id}/cover"
        assert (
            client.put(
                source_path,
                headers=auth_headers,
                files={"file": ("source.png", png(), "image/png")},
            ).status_code
            == 200
        )
        before = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        winner = upload(data=png(width=16))

        result = client.post(
            f"/api/v1/models/{model.id}/cover/source",
            json={"source_id": source.id},
            headers={
                **auth_headers,
                "X-PrintStash-Edit-Contract": "conditional-v1",
                "If-Match": before.headers["etag"],
            },
        )

        assert result.status_code == 412, result.text
        current = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        assert current.json()["thumbnail_url"] == winner.json()["thumbnail_url"]

    def test_cover_read_requires_collection_access(
        self,
        client,
        auth_headers,
        db_session,
        make_collection,
        make_model,
        user_headers,
    ):
        collection = make_collection("Private covers")
        private = make_model(collection=collection)
        outsider_headers = user_headers()
        db_session.rollback()
        saved = client.put(
            f"/api/v1/models/{private.id}/cover",
            headers=auth_headers,
            files={"file": ("cover.png", png(), "image/png")},
        )
        assert saved.status_code == 200, saved.text

        result = client.get(saved.json()["thumbnail_url"], headers=outsider_headers)

        assert result.status_code == 403, result.text
