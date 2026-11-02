"""Preview routes obey the same live Model and share authorization boundaries."""

import hashlib

import pytest
import trimesh

from app.core.time import utcnow
from app.db.models import CollectionRole, DerivativeKind, FileType
from app.modules.library.model_views.previews import generation
from app.modules.media.preview_views import render_views
from app.modules.storage.storage_backend.runtime import get_backend


@pytest.fixture(scope="module")
def rendered():
    return render_views(
        trimesh.creation.box(extents=(10, 12, 14)), source_sha256="a" * 64
    )


@pytest.fixture
def gallery(make_collection, make_model, make_file, make_derivative, rendered):
    collection = make_collection("Preview access")
    model = make_model(collection_id=collection.id)
    file = make_file(
        model, filename="model.stl", file_type=FileType.STL, sha256="a" * 64
    )
    manifest, images = rendered
    backend = get_backend()
    key = backend.blob_key("_derivatives", file.id, "previews.bin")
    backend.write_bytes(b"".join(images), key)
    derivative = make_derivative(
        file,
        DerivativeKind.PREVIEWS,
        storage_key=key,
        output_json=manifest.model_dump_json(),
    )
    root = f"/api/v1/models/{model.id}/previews/{generation(derivative)}"
    return model, file, collection, root, images, derivative


class TestPreviewReads:
    def test_serves_exact_published_bytes(self, client, auth_headers, gallery):
        _, _, _, root, images, _ = gallery
        response = client.get(root + "/orbit-0", headers=auth_headers)
        assert response.status_code == 200, response.text
        assert response.content == images[0]
        assert (
            response.headers["etag"]
            == '"' + hashlib.sha256(images[0]).hexdigest() + '"'
        )

    def test_advertises_manifest_in_model_reads(self, client, auth_headers, gallery):
        model, file, _, root, _, _ = gallery
        response = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        previews = response.json()["previews"]
        assert previews["kind"] == "artifact"
        assert previews["published"]["manifest_url"] == root
        assert previews["file_id"] == file.id

    @pytest.mark.parametrize("suffix", ["", "/orbit-0"])
    def test_denies_unauthenticated_reads(self, client, gallery, suffix):
        assert client.get(gallery[3] + suffix).status_code == 401

    @pytest.mark.parametrize("suffix", ["", "/orbit-0"])
    def test_denies_collection_outsiders(
        self, client, gallery, make_user, headers_for, suffix
    ):
        assert (
            client.get(
                gallery[3] + suffix, headers=headers_for(make_user())
            ).status_code
            == 403
        )

    def test_allows_collection_viewers(
        self, client, gallery, make_user, headers_for, grant_role
    ):
        viewer = make_user()
        grant_role(viewer, gallery[2], CollectionRole.VIEW)
        assert (
            client.get(gallery[3] + "/orbit-0", headers=headers_for(viewer)).status_code
            == 200
        )

    @pytest.mark.parametrize("subject", ["model", "file"])
    def test_hides_trashed_subjects(
        self, client, auth_headers, gallery, db_session, subject
    ):
        row = gallery[0] if subject == "model" else gallery[1]
        row.deleted_at = utcnow()
        db_session.add(row)
        db_session.commit()
        assert (
            client.get(gallery[3] + "/orbit-0", headers=auth_headers).status_code == 404
        )

    def test_rejects_an_unpublished_view(self, client, auth_headers, gallery):
        assert client.get(gallery[3] + "/face", headers=auth_headers).status_code == 404

    def test_rejects_an_unknown_generation(self, client, auth_headers, gallery):
        model = gallery[0]
        assert (
            client.get(
                f"/api/v1/models/{model.id}/previews/unknown/orbit-0",
                headers=auth_headers,
            ).status_code
            == 404
        )

    def test_reports_missing_published_bytes(self, client, auth_headers, gallery):
        backend = get_backend()
        path = backend.direct_path(gallery[5].storage_key)
        assert path is not None
        path.unlink()
        assert (
            client.get(gallery[3] + "/orbit-0", headers=auth_headers).status_code == 410
        )

    def test_reports_corrupt_published_bytes(self, client, auth_headers, gallery):
        path = get_backend().direct_path(gallery[5].storage_key)
        assert path is not None
        path.write_bytes(b"corrupt")
        assert (
            client.get(gallery[3] + "/orbit-0", headers=auth_headers).status_code == 410
        )

    def test_shares_only_the_selected_models_previews(
        self, client, auth_headers, gallery
    ):
        model, _, _, _, images, _ = gallery
        created = client.post(
            f"/api/v1/models/{model.id}/shares", json={}, headers=auth_headers
        )
        assert created.status_code == 200, created.text
        detail = client.get(f"/api/v1/share/{created.json()['token']}").json()
        url = detail["previews"]["published"]["views"][0]["url"]
        assert url.startswith(f"/api/v1/share/{created.json()['token']}/previews/")
        response = client.get(url)
        assert response.status_code == 200
        assert response.content == images[0]
        assert response.headers["cache-control"] == "private, no-store"

    def test_revocation_denies_preview_bytes(self, client, auth_headers, gallery):
        model = gallery[0]
        created = client.post(
            f"/api/v1/models/{model.id}/shares", json={}, headers=auth_headers
        ).json()
        detail = client.get(f"/api/v1/share/{created['token']}").json()
        url = detail["previews"]["published"]["views"][0]["url"]
        assert (
            client.delete(
                f"/api/v1/shares/{created['id']}", headers=auth_headers
            ).status_code
            == 200
        )
        # Revoked tokens share the uniform not-found answer of every share route.
        assert client.get(url).status_code == 404

    def test_card_arrivals_reference_only_the_current_artifact(
        self, client, auth_headers, gallery
    ):
        model, file, _, root, _, _ = gallery
        response = client.get(
            f"/api/v1/models/browse/thumbnails?model_id={model.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text
        preview = response.json()["items"][0]["previews"]
        assert preview["file_id"] == file.id
        assert preview["published"]["manifest_url"] == root
