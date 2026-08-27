"""Defends ``test_source_cover_is_private_normalized_and_metadata_is_available`` behavior for the ``schemas`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import io
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ProvenanceCapture,
)


def _model(session: Session) -> Model:
    model = Model(name="Bracket", slug="bracket", hash="a" * 64)
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def _source_with_capture(session: Session, model_id: int) -> ModelProvenanceSource:
    now = utcnow()
    source_item_id = f"api-{uuid.uuid4().hex}"
    source = ModelProvenanceSource(
        model_id=model_id,
        provider="printables",
        source_item_id=source_item_id,
        canonical_url=f"https://www.printables.com/model/{source_item_id}-bracket",
        identity_key=uuid.uuid4().hex * 2,
        first_captured_at=now,
        last_checked_at=now,
        updated_at=now,
    )
    session.add(source)
    session.flush()
    session.add(
        ModelProvenanceField(
            provenance_source_id=source.id,
            field_name="title",
            captured_value_json='"Captured Bracket"',
            captured_origin="confirmed",
            captured_at=now,
        )
    )
    session.add(
        ProvenanceCapture(
            provenance_source_id=source.id,
            adapter_version="printables-v1",
            snapshot_json='{"sensitive":"must not be returned"}',
            snapshot_sha256="c" * 64,
            captured_at=now,
            checked_at=now,
        )
    )
    session.commit()
    return source


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "navy").save(output, format="PNG")
    return output.getvalue()


def test_source_cover_is_private_normalized_and_metadata_is_available(
    client: TestClient,
    app: FastAPI,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    model = _model(db_session)
    source = _source_with_capture(db_session, model.id)
    model_id = model.id
    source_id = source.id
    assert model_id is not None and source_id is not None
    # ``commit()`` expires ORM rows. Materialize the scalar IDs, then release
    # the StaticPool connection before TestClient runs the request on its own
    # thread/session; never share a Session across that boundary.
    db_session.rollback()
    assert not db_session.in_transaction()
    base = f"/api/v1/models/{model_id}/provenance/{source_id}/cover"
    uploaded = client.put(
        base,
        headers=auth_headers,
        files={"file": ("cover.png", _png(), "image/png")},
    )

    assert uploaded.status_code == 200
    assert uploaded.json()["content_type"] == "image/webp"
    metadata = client.get(base, headers=auth_headers)
    assert metadata.status_code == 200
    streamed = client.get(f"{base}/content", headers=auth_headers)
    assert streamed.status_code == 200
    assert streamed.headers["cache-control"] == "private, max-age=3600"
    assert streamed.headers["content-type"] == "image/webp"


def test_provenance_detail_returns_safe_effective_field_and_capture_summaries(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    model = _model(db_session)
    source = _source_with_capture(db_session, model.id)

    response = client.get(f"/api/v1/models/{model.id}/provenance", headers=auth_headers)

    assert response.status_code == 200
    read = next(item for item in response.json()["sources"] if item["id"] == source.id)
    assert {
        key: read[key]
        for key in (
            "id",
            "provider",
            "source_item_id",
            "canonical_url",
            "source_revision",
        )
    } == {
        "id": source.id,
        "provider": "printables",
        "source_item_id": source.source_item_id,
        "canonical_url": source.canonical_url,
        "source_revision": None,
    }
    assert read["fields"] == [
        {
            "field_name": "title",
            "captured_value": "Captured Bracket",
            "captured_origin": "confirmed",
            "user_value": None,
            "user_override_set": False,
            "effective_value": "Captured Bracket",
            "effective_origin": "confirmed",
            "captured_at": read["fields"][0]["captured_at"],
            "user_updated_at": None,
        }
    ]
    assert {
        key: read["captures"][0][key]
        for key in ("adapter_version", "source_revision", "snapshot_sha256")
    } == {
        "adapter_version": "printables-v1",
        "source_revision": None,
        "snapshot_sha256": "c" * 64,
    }
    serialized = response.text
    assert "sensitive" not in serialized
    assert "snapshot_json" not in serialized
    assert "captured_by" not in serialized
    assert "user_updated_by" not in serialized


def test_provenance_override_and_clear_are_edit_scoped_and_leave_legacy_source_url_alone(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    model = _model(db_session)
    model.source_url = "https://legacy.example/model/bracket"
    db_session.add(model)
    db_session.commit()
    source = _source_with_capture(db_session, model.id)

    updated = client.patch(
        f"/api/v1/models/{model.id}/provenance/{source.id}",
        headers=auth_headers,
        json={"overrides": {"title": "My Bracket"}},
    )

    assert updated.status_code == 200
    title = next(item for item in updated.json()["sources"] if item["id"] == source.id)[
        "fields"
    ][0]
    assert title["user_value"] == "My Bracket"
    assert title["effective_value"] == "My Bracket"
    assert title["user_override_set"] is True
    assert title["effective_origin"] == "user"
    assert client.get(f"/api/v1/models/{model.id}", headers=auth_headers).json()[
        "source_url"
    ] == ("https://legacy.example/model/bracket")

    cleared = client.patch(
        f"/api/v1/models/{model.id}/provenance/{source.id}",
        headers=auth_headers,
        json={"clear_overrides": ["title"]},
    )

    assert cleared.status_code == 200
    title = next(item for item in cleared.json()["sources"] if item["id"] == source.id)[
        "fields"
    ][0]
    assert title["user_value"] is None
    assert title["user_override_set"] is False
    assert title["effective_value"] == "Captured Bracket"
    assert title["effective_origin"] == "confirmed"
