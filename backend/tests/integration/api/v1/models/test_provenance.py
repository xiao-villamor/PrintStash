"""Model provenance routes expose safe snapshots and privately managed covers."""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelSourceCover,
    ProvenanceCapture,
    StorageDeleteIntent,
)


def _model(db_session: Session, suffix: str) -> Model:
    model = Model(
        name=f"Provenance {suffix}",
        slug=f"provenance-{suffix}",
        hash=f"{suffix:0<64}"[:64],
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    return model


def _source(db_session: Session, model: Model) -> ModelProvenanceSource:
    now = utcnow()
    source = ModelProvenanceSource(
        model_id=model.id,
        provider="printables",
        source_item_id=f"item-{model.id}",
        canonical_url=f"https://www.printables.com/model/{model.id}-fixture",
        identity_key=uuid.uuid4().hex * 2,
        first_captured_at=now,
        last_checked_at=now,
        updated_at=now,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add(
        ModelProvenanceField(
            provenance_source_id=source.id,
            field_name="title",
            captured_value_json='"Captured Bracket"',
            captured_origin="confirmed",
            captured_at=now,
        )
    )
    db_session.add(
        ProvenanceCapture(
            provenance_source_id=source.id,
            adapter_version="printables-v1",
            snapshot_json='{"secret":"must not be returned"}',
            snapshot_sha256="c" * 64,
            captured_at=now,
            checked_at=now,
        )
    )
    db_session.commit()
    db_session.refresh(source)
    return source


def _png(color: str = "navy") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format="PNG")
    return output.getvalue()


def _cover_url(model_id: int, source_id: int) -> str:
    return f"/api/v1/models/{model_id}/provenance/{source_id}/cover"


class TestGetModelProvenance:
    def test_returns_normalized_provenance_for_an_accessible_model(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "detail")
        source = _source(db_session, model)

        response = client.get(
            f"/api/v1/models/{model.id}/provenance", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["schema_version"] == 2
        assert response.json()["sources"][0]["id"] == source.id
        assert (
            response.json()["sources"][0]["fields"][0]["effective_value"]
            == "Captured Bracket"
        )
        assert (
            response.json()["sources"][0]["captures"][0]["snapshot_sha256"] == "c" * 64
        )
        assert "secret" not in response.text
        assert "snapshot_json" not in response.text

    def test_returns_an_empty_provenance_collection_when_none_exists(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "empty")

        response = client.get(
            f"/api/v1/models/{model.id}/provenance", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"schema_version": 2, "sources": []}

    def test_hides_missing_and_trashed_models_from_provenance(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "trashed")
        model.deleted_at = utcnow()
        db_session.add(model)
        db_session.commit()

        missing = client.get("/api/v1/models/999999/provenance", headers=auth_headers)
        trashed = client.get(
            f"/api/v1/models/{model.id}/provenance", headers=auth_headers
        )

        assert missing.status_code == 404, missing.text
        assert trashed.status_code == 404, trashed.text


class TestPatchModelProvenance:
    def test_sets_explicit_provenance_overrides(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "override")
        source = _source(db_session, model)

        response = client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            headers=auth_headers,
            json={"overrides": {"title": ""}},
        )

        field = response.json()["sources"][0]["fields"][0]
        assert response.status_code == 200, response.text
        assert field["user_override_set"] is True
        assert field["effective_value"] == ""
        assert field["effective_origin"] == "user"

    def test_clears_explicit_provenance_overrides(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "clear")
        source = _source(db_session, model)
        url = f"/api/v1/models/{model.id}/provenance/{source.id}"
        client.patch(url, headers=auth_headers, json={"overrides": {"title": "Mine"}})

        response = client.patch(
            url, headers=auth_headers, json={"clear_overrides": ["title"]}
        )

        field = response.json()["sources"][0]["fields"][0]
        assert response.status_code == 200, response.text
        assert field["user_override_set"] is False
        assert field["effective_value"] == "Captured Bracket"

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"overrides": {"unknown": "value"}}, id="unknown-field"),
            pytest.param(
                {"overrides": {"title": "value"}, "clear_overrides": ["title"]},
                id="set-clear-conflict",
            ),
            pytest.param({"clear_overrides": ["title", "title"]}, id="duplicate-clear"),
            pytest.param({"unexpected": True}, id="extra-field"),
        ],
    )
    def test_rejects_unknown_excessive_or_conflicting_provenance_override_fields(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        payload: dict,
    ) -> None:
        model = _model(db_session, f"invalid-{len(str(payload))}")
        source = _source(db_session, model)

        response = client.patch(
            f"/api/v1/models/{model.id}/provenance/{source.id}",
            headers=auth_headers,
            json=payload,
        )

        assert response.status_code == 422, response.text

    def test_hides_foreign_or_missing_source_ids_during_provenance_patch(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        first = _model(db_session, "first")
        second = _model(db_session, "second")
        foreign = _source(db_session, second)

        foreign_response = client.patch(
            f"/api/v1/models/{first.id}/provenance/{foreign.id}",
            headers=auth_headers,
            json={"overrides": {"title": "Mine"}},
        )
        missing_response = client.patch(
            f"/api/v1/models/{first.id}/provenance/999999",
            headers=auth_headers,
            json={"overrides": {"title": "Mine"}},
        )

        assert foreign_response.status_code == 404, foreign_response.text
        assert missing_response.status_code == 404, missing_response.text


class TestGetModelSourceCover:
    def test_returns_model_source_cover_metadata(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "cover-meta")
        source = _source(db_session, model)
        url = _cover_url(model.id, source.id)
        uploaded = client.put(
            url,
            headers=auth_headers,
            files={"file": ("cover.png", _png(), "image/png")},
        )

        response = client.get(url, headers=auth_headers)

        assert uploaded.status_code == 200, uploaded.text
        assert response.status_code == 200, response.text
        assert response.json()["content_type"] == "image/webp"
        assert "storage_key" not in response.json()

    def test_reports_absent_model_source_cover_metadata(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "cover-absent")
        source = _source(db_session, model)

        response = client.get(_cover_url(model.id, source.id), headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "source_cover_not_found"


class TestStreamModelSourceCover:
    def test_streams_model_source_cover_content(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "cover-stream")
        source = _source(db_session, model)
        url = _cover_url(model.id, source.id)
        client.put(
            url,
            headers=auth_headers,
            files={"file": ("cover.png", _png(), "image/png")},
        )

        response = client.get(f"{url}/content", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/webp"
        assert response.headers["cache-control"] == "private, max-age=3600"
        assert response.headers["etag"].startswith('"source-cover-')
        assert response.content.startswith(b"RIFF")

    def test_returns_safe_not_found_for_missing_model_source_cover_bytes(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "cover-stream-missing")
        source = _source(db_session, model)

        response = client.get(
            f"{_cover_url(model.id, source.id)}/content", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "source_cover_not_found"


class TestPutModelSourceCover:
    def test_uploads_a_valid_model_source_cover_through_the_cover_lifecycle(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "cover-put")
        source = _source(db_session, model)

        response = client.put(
            _cover_url(model.id, source.id),
            headers=auth_headers,
            files={"file": ("cover.png", _png(), "image/png")},
        )

        assert response.status_code == 200, response.text
        assert response.json()["provenance_source_id"] == source.id
        assert response.json()["content_type"] == "image/webp"
        assert response.json()["size_bytes"] > 0

    def test_replaces_an_existing_model_source_cover_without_duplicate_rows(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "cover-replace")
        source = _source(db_session, model)
        url = _cover_url(model.id, source.id)
        first = client.put(
            url,
            headers=auth_headers,
            files={"file": ("first.png", _png("navy"), "image/png")},
        )

        second = client.put(
            url,
            headers=auth_headers,
            files={"file": ("second.png", _png("gold"), "image/png")},
        )

        rows = db_session.exec(
            select(ModelSourceCover).where(
                ModelSourceCover.provenance_source_id == source.id
            )
        ).all()
        assert second.status_code == 200, second.text
        assert second.json()["id"] == first.json()["id"]
        assert len(rows) == 1

    @pytest.mark.parametrize(
        ("payload", "content_type"),
        [
            pytest.param(b"not-an-image", "image/png", id="malformed"),
            pytest.param(_png(), "text/plain", id="unsupported-content-type"),
        ],
    )
    def test_rejects_malformed_model_source_cover_upload(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        payload: bytes,
        content_type: str,
    ) -> None:
        model = _model(db_session, f"cover-invalid-{content_type.replace('/', '-')}")
        source = _source(db_session, model)

        response = client.put(
            _cover_url(model.id, source.id),
            headers=auth_headers,
            files={"file": ("cover.bin", payload, content_type)},
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(ModelSourceCover)).first() is None


class TestDeleteModelSourceCover:
    def test_deletes_model_source_cover_metadata_and_queues_owned_bytes(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "cover-delete")
        source = _source(db_session, model)
        url = _cover_url(model.id, source.id)
        uploaded = client.put(
            url,
            headers=auth_headers,
            files={"file": ("cover.png", _png(), "image/png")},
        )

        response = client.delete(url, headers=auth_headers)

        intent = db_session.exec(
            select(StorageDeleteIntent).where(
                StorageDeleteIntent.resource_kind == "model_source_cover",
                StorageDeleteIntent.resource_id == uploaded.json()["id"],
            )
        ).one()
        assert response.status_code == 204, response.text
        assert db_session.get(ModelSourceCover, uploaded.json()["id"]) is None
        assert intent.status == "pending"
        assert intent.token

    def test_returns_not_found_when_deleting_an_absent_model_source_cover(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        model = _model(db_session, "cover-delete-absent")
        source = _source(db_session, model)

        response = client.delete(_cover_url(model.id, source.id), headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "source_cover_not_found"


class TestModelSourceCoverAccess:
    def test_enforces_model_and_source_access_on_every_cover_endpoint(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        first = _model(db_session, "cover-access-first")
        second = _model(db_session, "cover-access-second")
        foreign = _source(db_session, second)
        url = _cover_url(first.id, foreign.id)

        get_response = client.get(url, headers=auth_headers)
        stream_response = client.get(f"{url}/content", headers=auth_headers)
        put_response = client.put(
            url,
            headers=auth_headers,
            files={"file": ("cover.png", _png(), "image/png")},
        )
        delete_response = client.delete(url, headers=auth_headers)

        assert get_response.status_code == 404, get_response.text
        assert stream_response.status_code == 404, stream_response.text
        assert put_response.status_code == 404, put_response.text
        assert delete_response.status_code == 404, delete_response.text
