"""Defends public share access counts, liveness, and request throttling.

A bearer share token must never revive trashed data or become an unbounded
enumeration surface, while successful anonymous reads remain observable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.v1.share import router as public_share_router
from app.core.time import utcnow
from app.db.models import File, Model, ShareLink


def _make_model(db_session: Session, *, slug: str, hash_: str) -> Model:
    model = Model(name="Shared model", slug=slug, hash=hash_)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    return model


def _make_file(
    db_session: Session,
    model: Model,
    *,
    filename: str,
    file_type: str,
) -> File:
    file_row = File(
        model_id=model.id,
        path=f"/nonexistent/{filename}",
        original_filename=filename,
        file_type=file_type,
        version=1,
        size_bytes=10,
        sha256="b" * 64,
    )
    db_session.add(file_row)
    db_session.commit()
    db_session.refresh(file_row)
    return file_row


def _create_share(
    client: TestClient,
    auth_headers: dict[str, str],
    model_id: int,
    *,
    allow_download: bool = False,
) -> dict:
    response = client.post(
        f"/api/v1/models/{model_id}/shares",
        json={"allow_download": allow_download},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _exhaust_public_share_limit(client: TestClient, token: str) -> None:
    for _ in range(120):
        response = client.get(f"/api/v1/share/{token}")
        assert response.status_code == 200, response.text


class TestGetSharedModelBoundaries:
    def test_increments_access_count_for_a_successful_public_read(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="counted-access", hash_="7" * 64)
        created = _create_share(client, auth_headers, model.id)

        response = client.get(f"/api/v1/share/{created['token']}")

        assert response.status_code == 200, response.text
        link = db_session.get(ShareLink, created["id"])
        assert link is not None
        db_session.refresh(link)
        assert link.access_count == 1

    def test_hides_a_model_trashed_after_link_creation(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="trashed-public", hash_="8" * 64)
        created = _create_share(client, auth_headers, model.id)
        model.deleted_at = utcnow()
        db_session.add(model)
        db_session.commit()

        response = client.get(f"/api/v1/share/{created['token']}")

        assert response.status_code == 404
        link = db_session.get(ShareLink, created["id"])
        assert link is not None
        assert link.access_count == 0

    def test_rate_limits_repeated_public_reads_per_client_ip(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        model = _make_model(db_session, slug="rate-limited", hash_="a" * 64)
        created = _create_share(client, auth_headers, model.id)
        limiter = public_share_router.dependencies[0].dependency.limiter
        limiter.reset()

        try:
            _exhaust_public_share_limit(client, created["token"])
            response = client.get(f"/api/v1/share/{created['token']}")
        finally:
            limiter.reset()

        assert response.status_code == 429
        assert response.json()["detail"] == "rate_limited"


class TestSharedFileLiveness:
    @pytest.mark.parametrize(
        ("route", "file_type", "allow_download"),
        [
            pytest.param("stl", "stl", False, id="mesh-view"),
            pytest.param("download", "stl", True, id="original-download"),
            pytest.param("gcode", "gcode", True, id="gcode-preview"),
        ],
    )
    def test_hides_trashed_artifacts_from_every_public_file_route(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        route: str,
        file_type: str,
        allow_download: bool,
    ) -> None:
        model = _make_model(db_session, slug=f"trashed-{route}", hash_="9" * 64)
        file_row = _make_file(
            db_session,
            model,
            filename=f"trashed.{file_type}",
            file_type=file_type,
        )
        file_row.deleted_at = utcnow()
        db_session.add(file_row)
        db_session.commit()
        created = _create_share(
            client, auth_headers, model.id, allow_download=allow_download
        )

        response = client.get(
            f"/api/v1/share/{created['token']}/files/{file_row.id}/{route}"
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "not_found"
