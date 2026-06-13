from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import Model


def _create_model(db_session: Session) -> Model:
    model = Model(
        name="Benchy",
        slug="benchy",
        hash="a" * 64,
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    return model


def test_model_source_url_can_be_attached_and_cleared(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    model = _create_model(db_session)

    updated = client.patch(
        f"/api/v1/models/{model.id}",
        headers=auth_headers,
        json={"source_url": " https://www.printables.com/model/123-benchy "},
    )

    assert updated.status_code == 200
    assert updated.json()["source_url"] == "https://www.printables.com/model/123-benchy"

    detail = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["source_url"] == "https://www.printables.com/model/123-benchy"

    export = client.get("/api/v1/models/export", headers=auth_headers)
    assert export.status_code == 200
    assert export.json()["models"][0]["source_url"] == (
        "https://www.printables.com/model/123-benchy"
    )

    cleared = client.patch(
        f"/api/v1/models/{model.id}",
        headers=auth_headers,
        json={"source_url": ""},
    )

    assert cleared.status_code == 200
    assert cleared.json()["source_url"] is None


def test_model_source_url_rejects_non_http_urls(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    model = _create_model(db_session)

    response = client.patch(
        f"/api/v1/models/{model.id}",
        headers=auth_headers,
        json={"source_url": "ftp://example.com/model.zip"},
    )

    assert response.status_code == 422
