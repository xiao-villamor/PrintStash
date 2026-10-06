"""Where a model came from, and the cover image the source showed for it.

Provenance is a *record*, not an editable field: what the capture saw is kept exactly as
it was seen, and a user's correction is stored beside it as an override rather than on top
of it. That is what makes "the site changed the title" and "I renamed it" tell apart, so
every read here asserts the captured value, the user value and the effective value as
three separate things.

What must never leave the API is the raw capture snapshot — it can hold whatever the page
carried, including things the user never meant to store — and the actor ids on the
override rows. Both are asserted absent from the serialized response.

The cover image is private: it is streamed through the app with `Cache-Control: private`
rather than handed out as a URL, and its ETag changes when the image does.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    Collection,
    CollectionRole,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelSourceCover,
    OwnedStorageObject,
    ProvenanceCapture,
    StorageDeleteIntent,
    StorageObjectState,
)
from app.db.session import get_session_factory, override_session_factory
from app.modules.media.source_cover_processing import process_source_cover_upload
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories import build_collection, build_model, grant_collection_role
from tests.fakes.thread_sessions import ThreadBoundSessionFactory

SECRET_IN_SNAPSHOT = "must-not-be-returned"


def _png(color: str = "navy") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def model(db_session: Session) -> Model:
    row = build_model(
        db_session,
        name="Bracket",
        slug=f"bracket-{uuid.uuid4().hex[:8]}",
        hash="a" * 64,
    )
    return row


@pytest.fixture
def source(db_session: Session, model: Model) -> ModelProvenanceSource:
    """One captured source for `model`, with a captured field and a raw snapshot."""
    now = utcnow()
    source_item_id = f"api-{uuid.uuid4().hex}"
    row = ModelProvenanceSource(
        model_id=model.id,
        provider="printables",
        source_item_id=source_item_id,
        canonical_url=f"https://www.printables.com/model/{source_item_id}-bracket",
        identity_key=uuid.uuid4().hex * 2,
        first_captured_at=now,
        last_checked_at=now,
        updated_at=now,
    )
    db_session.add(row)
    db_session.flush()
    db_session.add(
        ModelProvenanceField(
            provenance_source_id=row.id,
            field_name="title",
            captured_value_json='"Captured Bracket"',
            captured_origin="confirmed",
            captured_at=now,
        )
    )
    db_session.add(
        ProvenanceCapture(
            provenance_source_id=row.id,
            adapter_version="printables-v1",
            snapshot_json=f'{{"sensitive":"{SECRET_IN_SNAPSHOT}"}}',
            snapshot_sha256="c" * 64,
            captured_at=now,
            checked_at=now,
        )
    )
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture
def cover_url(model: Model, source: ModelProvenanceSource) -> str:
    return f"/api/v1/models/{model.id}/provenance/{source.id}/cover"


@pytest.fixture
def uploaded_cover(
    client: TestClient, db_session: Session, cover_url: str, auth_headers
) -> str:
    """A cover already stored for the source, with the session released first."""
    db_session.rollback()
    response = client.put(
        cover_url,
        headers=auth_headers,
        files={"file": ("cover.png", _png(), "image/png")},
    )
    assert response.status_code == 200, response.text
    return cover_url


@pytest.fixture
def viewer(db_session: Session, model: Model, make_user, headers_for):
    """Grant a fresh user a role on the model's collection and return its headers."""

    def grant(username: str, role: CollectionRole) -> dict[str, str]:
        collection = db_session.exec(select(Collection)).first()
        if collection is None:
            collection = build_collection(
                db_session, name="Shelf", slug="shelf", path="shelf"
            )
        target = db_session.get(Model, model.id)
        assert target is not None
        target.collection_id = collection.id
        user = make_user(username)
        grant_collection_role(db_session, user, collection, role)
        return headers_for(user)

    return grant


def _source_read(body: dict[str, Any], source_id: int) -> dict[str, Any]:
    return next(item for item in body["sources"] if item["id"] == source_id)


class TestGetModelProvenance:
    def test_returns_the_source_it_captured_from(
        self,
        client: TestClient,
        model: Model,
        source: ModelProvenanceSource,
        auth_headers,
    ) -> None:
        response = client.get(
            f"/api/v1/models/{model.id}/provenance", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        read = _source_read(response.json(), source.id)
        assert read["provider"] == "printables"
        assert read["canonical_url"] == source.canonical_url

    def test_reports_a_captured_field_as_captured_with_no_user_value(
        self,
        client: TestClient,
        model: Model,
        source: ModelProvenanceSource,
        auth_headers,
    ) -> None:
        response = client.get(
            f"/api/v1/models/{model.id}/provenance", headers=auth_headers
        )

        field = _source_read(response.json(), source.id)["fields"][0]
        assert field["captured_value"] == "Captured Bracket"
        assert field["user_value"] is None
        assert field["user_override_set"] is False
        assert field["effective_value"] == "Captured Bracket"
        assert field["effective_origin"] == "confirmed"

    def test_summarizes_each_capture(
        self,
        client: TestClient,
        model: Model,
        source: ModelProvenanceSource,
        auth_headers,
    ) -> None:
        response = client.get(
            f"/api/v1/models/{model.id}/provenance", headers=auth_headers
        )

        capture = _source_read(response.json(), source.id)["captures"][0]
        assert capture["adapter_version"] == "printables-v1"
        assert capture["snapshot_sha256"] == "c" * 64

    def test_never_returns_the_raw_capture_snapshot(
        self,
        client: TestClient,
        model: Model,
        source: ModelProvenanceSource,
        auth_headers,
    ) -> None:
        response = client.get(
            f"/api/v1/models/{model.id}/provenance", headers=auth_headers
        )

        # The snapshot holds whatever the page carried.
        assert SECRET_IN_SNAPSHOT not in response.text
        assert "snapshot_json" not in response.text

    def test_never_returns_who_captured_or_overrode_a_field(
        self,
        client: TestClient,
        model: Model,
        source: ModelProvenanceSource,
        auth_headers,
    ) -> None:
        response = client.get(
            f"/api/v1/models/{model.id}/provenance", headers=auth_headers
        )

        assert "captured_by" not in response.text
        assert "user_updated_by" not in response.text

    def test_reports_a_model_that_does_not_exist(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.get("/api/v1/models/9999/provenance", headers=auth_headers)

        assert response.status_code == 404, response.text

    def test_rejects_a_caller_with_no_role_on_the_collection(
        self,
        client: TestClient,
        model: Model,
        source: ModelProvenanceSource,
        user_headers,
    ) -> None:
        response = client.get(
            f"/api/v1/models/{model.id}/provenance", headers=user_headers("prov-nobody")
        )

        assert response.status_code in (403, 404), response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, model: Model
    ) -> None:
        assert client.get(f"/api/v1/models/{model.id}/provenance").status_code == 401


class TestPatchModelProvenance:
    def test_records_a_users_correction_beside_the_captured_value(
        self,
        client: TestClient,
        model: Model,
        source: ModelProvenanceSource,
        auth_headers,
    ) -> None:
        response = client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            headers=auth_headers,
            json={"overrides": {"title": "My Bracket"}},
        )

        assert response.status_code == 200, response.text
        field = _source_read(response.json(), source.id)["fields"][0]
        assert field["captured_value"] == "Captured Bracket"
        assert field["user_value"] == "My Bracket"
        assert field["effective_value"] == "My Bracket"
        assert field["effective_origin"] == "user"

    def test_leaves_the_legacy_source_url_alone(
        self,
        client: TestClient,
        db_session: Session,
        model: Model,
        source: ModelProvenanceSource,
        auth_headers,
    ) -> None:
        model.source_url = "https://legacy.example/model/bracket"
        db_session.add(model)
        db_session.commit()

        client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            headers=auth_headers,
            json={"overrides": {"title": "My Bracket"}},
        )

        detail = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        assert detail.json()["source_url"] == "https://legacy.example/model/bracket"

    def test_falls_back_to_the_captured_value_when_an_override_is_cleared(
        self,
        client: TestClient,
        model: Model,
        source: ModelProvenanceSource,
        auth_headers,
    ) -> None:
        client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            headers=auth_headers,
            json={"overrides": {"title": "My Bracket"}},
        )

        response = client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            headers=auth_headers,
            json={"clear_overrides": ["title"]},
        )

        field = _source_read(response.json(), source.id)["fields"][0]
        assert field["user_value"] is None
        assert field["user_override_set"] is False
        assert field["effective_value"] == "Captured Bracket"

    def test_refuses_a_contradictory_override_request(
        self,
        client: TestClient,
        model: Model,
        source: ModelProvenanceSource,
        auth_headers,
    ) -> None:
        response = client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            headers=auth_headers,
            json={"overrides": {"title": "My Bracket"}, "clear_overrides": ["title"]},
        )

        # Applying both in either order gives a different answer, so neither runs.
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "provenance_override_conflict"

    @pytest.mark.parametrize(
        ("body", "reason"),
        [
            pytest.param(
                {"overrides": {"owner_id": "1"}},
                "unsupported_provenance_field",
                id="unknown-override",
            ),
            pytest.param(
                {"overrides": {"title": "x" * 100_000}},
                "provenance_override_too_large",
                id="oversized-override",
            ),
            pytest.param(
                {"overrides": {"title": "line\nbreak"}},
                "invalid_provenance_override",
                id="control-character",
            ),
            pytest.param(
                {"clear_overrides": ["title", "title"]},
                "unsupported_provenance_field",
                id="duplicate-clear",
            ),
            pytest.param(
                {"clear_overrides": ["owner_id"]},
                "unsupported_provenance_field",
                id="unknown-clear",
            ),
        ],
    )
    def test_refuses_an_override_outside_the_allowlist(
        self,
        client: TestClient,
        model: Model,
        source: ModelProvenanceSource,
        auth_headers,
        body: dict,
        reason: str,
    ) -> None:
        response = client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            headers=auth_headers,
            json=body,
        )

        assert response.status_code == 422
        assert reason in response.text

    def test_writes_nothing_when_it_refuses_a_conflicting_patch(
        self,
        client: TestClient,
        db_session: Session,
        model: Model,
        source: ModelProvenanceSource,
        auth_headers,
    ) -> None:
        client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            headers=auth_headers,
            json={"overrides": {"title": "My Bracket"}, "clear_overrides": ["title"]},
        )

        detail = client.get(
            f"/api/v1/models/{model.id}/provenance", headers=auth_headers
        )
        assert _source_read(detail.json(), source.id)["fields"][0]["user_value"] is None

    def test_reports_a_source_that_does_not_belong_to_this_model(
        self, client: TestClient, model: Model, auth_headers
    ) -> None:
        response = client.patch(
            f"/api/v1/models/{model.id}/provenance/9999",
            headers=auth_headers,
            json={"overrides": {"title": "Ghost"}},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "provenance_source_not_found"

    def test_rejects_a_caller_who_may_only_view_the_collection(
        self, client: TestClient, model: Model, source: ModelProvenanceSource, viewer
    ) -> None:
        headers = viewer("prov-viewer", CollectionRole.VIEW)

        response = client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            headers=headers,
            json={"overrides": {"title": "Not mine to change"}},
        )

        assert response.status_code == 403, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, model: Model, source: ModelProvenanceSource
    ) -> None:
        response = client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            json={"overrides": {"title": "Anonymous"}},
        )

        assert response.status_code == 401, response.text


class TestPutModelSourceCover:
    def test_stores_the_uploaded_image(
        self, client: TestClient, db_session: Session, cover_url: str, auth_headers
    ) -> None:
        db_session.rollback()

        response = client.put(
            cover_url,
            headers=auth_headers,
            files={"file": ("cover.png", _png(), "image/png")},
        )

        assert response.status_code == 200, response.text

    def test_normalizes_it_to_webp(
        self, client: TestClient, db_session: Session, cover_url: str, auth_headers
    ) -> None:
        db_session.rollback()

        response = client.put(
            cover_url,
            headers=auth_headers,
            files={"file": ("cover.png", _png(), "image/png")},
        )

        assert response.json()["content_type"] == "image/webp"

    def test_replaces_a_cover_that_is_already_there(
        self, client: TestClient, db_session: Session, uploaded_cover: str, auth_headers
    ) -> None:
        db_session.rollback()

        response = client.put(
            uploaded_cover,
            headers=auth_headers,
            files={"file": ("cover.png", _png("crimson"), "image/png")},
        )

        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert len(db_session.exec(select(ModelSourceCover)).all()) == 1

    def test_refuses_bytes_that_are_not_an_image(
        self, client: TestClient, db_session: Session, cover_url: str, auth_headers
    ) -> None:
        db_session.rollback()

        response = client.put(
            cover_url,
            headers=auth_headers,
            files={"file": ("cover.png", b"not-an-image", "image/png")},
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "source_cover_invalid"

    def test_retires_the_private_candidate_when_the_commit_fails(
        self,
        client: TestClient,
        db_session: Session,
        cover_url: str,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import models as models_api

        real_prepare = models_api.source_covers.prepare_candidate

        def prepare_then_break(session, backend, **kwargs):
            result = real_prepare(session, backend, **kwargs)
            monkeypatch.setattr(session, "commit", _explode)
            return result

        def _explode() -> None:
            raise RuntimeError("commit failed")

        monkeypatch.setattr(
            models_api.source_covers, "prepare_candidate", prepare_then_break
        )
        db_session.rollback()

        with pytest.raises(RuntimeError, match="commit failed"):
            client.put(
                cover_url,
                headers=auth_headers,
                files={"file": ("cover.png", _png(), "image/png")},
            )

        db_session.expire_all()
        assert db_session.exec(select(ModelSourceCover)).all() == []
        proof = db_session.exec(select(OwnedStorageObject)).one()
        intent = db_session.exec(select(StorageDeleteIntent)).one()
        assert proof.state is StorageObjectState.RETIRING
        assert intent.key == proof.key
        assert intent.token == proof.token
        assert intent.status == "pending"

    def test_preserves_the_committed_cover_when_the_commit_acknowledgement_is_lost(
        self,
        client: TestClient,
        db_session: Session,
        cover_url: str,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import models as models_api

        real_prepare = models_api.source_covers.prepare_candidate

        def prepare_then_lose_ack(session, backend, **kwargs):
            candidate = real_prepare(session, backend, **kwargs)
            commit = session.commit

            def commit_then_raise():
                commit()
                raise RuntimeError("commit acknowledgement lost")

            monkeypatch.setattr(session, "commit", commit_then_raise)
            return candidate

        monkeypatch.setattr(
            models_api.source_covers, "prepare_candidate", prepare_then_lose_ack
        )
        db_session.rollback()

        with pytest.raises(RuntimeError, match="commit acknowledgement lost"):
            client.put(
                cover_url,
                headers=auth_headers,
                files={"file": ("cover.png", _png(), "image/png")},
            )

        db_session.expire_all()
        cover = db_session.exec(select(ModelSourceCover)).one()
        proof = db_session.exec(select(OwnedStorageObject)).one()
        assert cover.storage_key == proof.key
        assert proof.state is StorageObjectState.COMMITTED
        assert (
            get_backend().read_bytes(cover.storage_key)
            == process_source_cover_upload(_png(), "image/png").data
        )
        assert db_session.exec(select(StorageDeleteIntent)).all() == []

    def test_reports_a_source_that_does_not_belong_to_this_model(
        self, client: TestClient, db_session: Session, model: Model, auth_headers
    ) -> None:
        db_session.rollback()

        response = client.put(
            f"/api/v1/models/{model.id}/provenance/9999/cover",
            headers=auth_headers,
            files={"file": ("cover.png", _png(), "image/png")},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "provenance_source_not_found"

    def test_rejects_a_caller_who_may_only_view_the_collection(
        self, client: TestClient, db_session: Session, cover_url: str, viewer
    ) -> None:
        headers = viewer("cover-viewer", CollectionRole.VIEW)
        db_session.rollback()

        response = client.put(
            cover_url,
            headers=headers,
            files={"file": ("cover.png", _png(), "image/png")},
        )

        assert response.status_code == 403, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, db_session: Session, cover_url: str
    ) -> None:
        db_session.rollback()

        response = client.put(
            cover_url, files={"file": ("cover.png", _png(), "image/png")}
        )

        assert response.status_code == 401, response.text


class TestGetModelSourceCover:
    def test_returns_the_covers_metadata(
        self, client: TestClient, uploaded_cover: str, auth_headers
    ) -> None:
        response = client.get(uploaded_cover, headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["content_type"] == "image/webp"

    def test_reports_a_source_with_no_cover(
        self, client: TestClient, db_session: Session, cover_url: str, auth_headers
    ) -> None:
        db_session.rollback()

        response = client.get(cover_url, headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "source_cover_not_found"

    def test_reports_a_source_that_does_not_belong_to_this_model(
        self, client: TestClient, model: Model, auth_headers
    ) -> None:
        response = client.get(
            f"/api/v1/models/{model.id}/provenance/9999/cover", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "provenance_source_not_found"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, uploaded_cover: str
    ) -> None:
        assert client.get(uploaded_cover).status_code == 401


class TestStreamModelSourceCover:
    def test_streams_the_image_itself(
        self, client: TestClient, uploaded_cover: str, auth_headers
    ) -> None:
        response = client.get(f"{uploaded_cover}/content", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/webp"
        assert response.content

    def test_keeps_the_image_out_of_shared_caches(
        self, client: TestClient, uploaded_cover: str, auth_headers
    ) -> None:
        response = client.get(f"{uploaded_cover}/content", headers=auth_headers)

        # A private library's cover must not be cacheable by a proxy.
        assert response.headers["cache-control"] == "private, max-age=3600"

    def test_carries_an_etag(
        self, client: TestClient, uploaded_cover: str, auth_headers
    ) -> None:
        response = client.get(f"{uploaded_cover}/content", headers=auth_headers)

        assert response.headers["etag"].startswith('"source-cover-')

    def test_reports_a_source_with_no_cover(
        self, client: TestClient, db_session: Session, cover_url: str, auth_headers
    ) -> None:
        db_session.rollback()

        response = client.get(f"{cover_url}/content", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "source_cover_not_found"

    def test_reports_a_source_that_does_not_belong_to_this_model(
        self, client: TestClient, model: Model, auth_headers
    ) -> None:
        response = client.get(
            f"/api/v1/models/{model.id}/provenance/9999/cover/content",
            headers=auth_headers,
        )

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, uploaded_cover: str
    ) -> None:
        assert client.get(f"{uploaded_cover}/content").status_code == 401


class TestDeleteModelSourceCover:
    def test_removes_the_cover(
        self, client: TestClient, db_session: Session, uploaded_cover: str, auth_headers
    ) -> None:
        response = client.delete(uploaded_cover, headers=auth_headers)

        assert response.status_code == 204, response.text
        db_session.expire_all()
        assert db_session.exec(select(ModelSourceCover)).all() == []

    def test_reports_a_source_with_no_cover(
        self, client: TestClient, db_session: Session, cover_url: str, auth_headers
    ) -> None:
        db_session.rollback()

        response = client.delete(cover_url, headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "source_cover_not_found"

    def test_reports_a_source_that_does_not_belong_to_this_model(
        self, client: TestClient, model: Model, auth_headers
    ) -> None:
        response = client.delete(
            f"/api/v1/models/{model.id}/provenance/9999/cover", headers=auth_headers
        )

        assert response.status_code == 404, response.text

    def test_rejects_a_caller_who_may_only_view_the_collection(
        self, client: TestClient, db_session: Session, uploaded_cover: str, viewer
    ) -> None:
        headers = viewer("cover-delete-viewer", CollectionRole.VIEW)
        db_session.rollback()

        response = client.delete(uploaded_cover, headers=headers)

        assert response.status_code == 403, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, uploaded_cover: str
    ) -> None:
        assert client.delete(uploaded_cover).status_code == 401


class TestCoverCommandOwnership:
    def test_upload_uses_thread_owned_sessions(
        self,
        client: TestClient,
        auth_headers,
        cover_url: str,
        db_session: Session,
    ) -> None:
        factory = ThreadBoundSessionFactory(db_session.get_bind())
        previous = get_session_factory()
        override_session_factory(factory)
        try:
            response = client.put(
                cover_url,
                headers=auth_headers,
                files={"file": ("owned.png", _png(), "image/png")},
            )
            assert response.status_code == 200, response.text
            assert response.json()["content_type"] == "image/webp"
            assert factory.opened_count >= 2
            assert factory.active_count == 0
        finally:
            override_session_factory(previous)

    @pytest.mark.asyncio
    async def test_keeps_health_responsive_during_publication(
        self,
        app,
        auth_headers,
        cover_url,
        monkeypatch,
    ):
        from app.api.v1 import models as model_api

        loop = asyncio.get_running_loop()
        observations = []
        publish = model_api.source_covers.prepare_candidate
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:

            def delayed_publication(*args, **kwargs):
                health = asyncio.run_coroutine_threadsafe(
                    client.get("/api/v1/health"), loop
                )
                try:
                    observations.append(health.result(timeout=2).status_code)
                except TimeoutError:
                    health.cancel()
                    observations.append(None)
                return publish(*args, **kwargs)

            monkeypatch.setattr(
                model_api.source_covers, "prepare_candidate", delayed_publication
            )
            response = await client.put(
                cover_url,
                headers=auth_headers,
                files={"file": ("owned.png", _png(), "image/png")},
            )
        assert response.status_code == 200, response.text
        assert observations == [200]


class TestConditionalProvenance:
    def test_rejects_stale_override(self, client, auth_headers, model, source):
        changed = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"name": "Concurrent name"},
        )
        assert changed.status_code == 200, changed.text

        response = client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            headers={**auth_headers, "If-Match": f'"model-{model.id}-v1"'},
            json={"overrides": {"title": "Old draft"}},
        )

        assert response.status_code == 412, response.text
        assert (
            client.get(f"/api/v1/models/{model.id}", headers=auth_headers).json()[
                "name"
            ]
            == "Concurrent name"
        )

    def test_conditions_source_cover_edit(self, client, auth_headers, model, source):
        from tests.factories.content import png

        uploaded = client.put(
            f"/api/v1/models/{model.id}/provenance/{source.id}/cover",
            headers={**auth_headers, "If-Match": f'"model-{model.id}-v1"'},
            files={"file": ("cover.png", png(), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text

        stale = client.delete(
            f"/api/v1/models/{model.id}/provenance/{source.id}/cover",
            headers={**auth_headers, "If-Match": f'"model-{model.id}-v1"'},
        )

        assert stale.status_code == 412, stale.text
        assert (
            client.get(
                f"/api/v1/models/{model.id}/provenance/{source.id}/cover",
                headers=auth_headers,
            ).status_code
            == 200
        )

    def test_conflicts_after_external_override_writer(
        self, client, auth_headers, model, source, db_session
    ):
        from sqlalchemy import text

        db_session.execute(
            text(
                "UPDATE model_provenance_fields SET user_value_json='\"External\"', user_override_set=1 WHERE provenance_source_id=:source"
            ),
            {"source": source.id},
        )
        db_session.commit()

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers={**auth_headers, "If-Match": f'"model-{model.id}-v1"'},
            json={"name": "Old draft"},
        )

        assert response.status_code == 412, response.text

    def test_keeps_version_for_capture_refresh(
        self, client, auth_headers, model, source, db_session
    ):
        from sqlalchemy import text

        db_session.execute(
            text(
                "UPDATE model_provenance_fields SET captured_value_json='\"Refreshed capture\"' WHERE provenance_source_id=:source"
            ),
            {"source": source.id},
        )
        db_session.commit()

        response = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["edit_version"] == 1
