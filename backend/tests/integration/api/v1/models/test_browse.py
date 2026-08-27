"""Browse routes wire canonical filters, visibility, facets, and pagination."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    Collection,
    File,
    FileRevisionStatus,
    FileType,
    Metadata,
    Model,
    ModelStar,
    ModelTagLink,
    PrintJob,
    PrintJobState,
    Tag,
)

from ._model_revisions_api_shared import _headers_for, _regular_user


def _browse_models(db_session: Session, user_id: int) -> tuple[Model, Model]:
    collection = Collection(
        name="Brackets", slug="brackets", path="functional/brackets"
    )
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    target = Model(
        name="Target Mount",
        slug="target-mount",
        hash="t" * 64,
        collection_id=collection.id,
        created_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
    )
    other = Model(name="Other Vase", slug="other-vase", hash="o" * 64)
    db_session.add_all([target, other])
    db_session.commit()
    db_session.refresh(target)
    db_session.refresh(other)
    pla = Tag(name="PLA", slug="pla")
    tested = Tag(name="Tested", slug="tested")
    db_session.add_all([pla, tested])
    db_session.commit()
    db_session.refresh(pla)
    db_session.refresh(tested)
    db_session.add_all(
        [
            ModelTagLink(model_id=target.id, tag_id=pla.id),
            ModelTagLink(model_id=target.id, tag_id=tested.id),
            ModelTagLink(model_id=other.id, tag_id=pla.id),
            ModelStar(user_id=user_id, model_id=target.id),
        ]
    )
    artifact = File(
        model_id=target.id,
        path="target.gcode",
        original_filename="target.gcode",
        file_type=FileType.GCODE,
        revision_status=FileRevisionStatus.KNOWN_GOOD,
        size_bytes=100,
        sha256="g" * 64,
        uploaded_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
    )
    other_artifact = File(
        model_id=other.id,
        path="other.stl",
        original_filename="other.stl",
        file_type=FileType.STL,
        size_bytes=50,
        sha256="s" * 64,
        is_external=True,
        uploaded_at=datetime(2030, 1, 15, tzinfo=timezone.utc),
    )
    db_session.add_all([artifact, other_artifact])
    db_session.commit()
    db_session.refresh(artifact)
    db_session.add(
        Metadata(
            file_id=artifact.id,
            material_type="PLA",
            slicer_name="OrcaSlicer",
            printer_model="MK4",
        )
    )
    db_session.add(
        PrintJob(
            model_id=target.id,
            file_id=artifact.id,
            remote_filename="target.gcode",
            state=PrintJobState.COMPLETED,
            finished_at=datetime(2026, 1, 20, tzinfo=timezone.utc),
        )
    )
    db_session.commit()
    return target, other


class TestListModels:
    def test_lists_accessible_live_models_with_the_canonical_browse_response(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        target, _other = _browse_models(db_session, 1)

        response = client.get("/api/v1/models?q=Target", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()] == [target.id]
        assert response.json()[0]["name"] == "Target Mount"

    def test_excludes_trashed_models_from_the_model_list(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        live, trashed = _browse_models(db_session, 1)
        trashed.deleted_at = utcnow()
        db_session.add(trashed)
        db_session.commit()

        response = client.get("/api/v1/models", headers=auth_headers)

        ids = {row["id"] for row in response.json()}
        assert live.id in ids
        assert trashed.id not in ids

    @pytest.mark.parametrize(
        ("query", "expected_slug"),
        [
            pytest.param(
                "collection=functional/brackets&direct=true",
                "target-mount",
                id="collection-direct",
            ),
            pytest.param("tag=pla&tag=tested", "target-mount", id="repeated-tags"),
            pytest.param("q=target", "target-mount", id="search"),
            pytest.param("favorites=true", "target-mount", id="favorites"),
            pytest.param("file_type=gcode", "target-mount", id="file-type"),
            pytest.param("material_type=PLA", "target-mount", id="material"),
            pytest.param("slicer_name=OrcaSlicer", "target-mount", id="slicer"),
            pytest.param("printer_model=MK4", "target-mount", id="printer-model"),
            pytest.param(
                "revision_status=known_good", "target-mount", id="revision-status"
            ),
            pytest.param("printed=true", "target-mount", id="printed"),
            pytest.param("print_outcome=completed", "target-mount", id="print-outcome"),
            pytest.param("storage=vault", "target-mount", id="storage"),
            pytest.param(
                "uploaded_after=2029-01-01T00:00:00Z", "other-vase", id="uploaded-after"
            ),
            pytest.param(
                "uploaded_before=2026-02-01T00:00:00Z",
                "target-mount",
                id="uploaded-before",
            ),
        ],
    )
    def test_filters_the_model_list_by_every_canonical_filter_group(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        query: str,
        expected_slug: str,
    ) -> None:
        target, other = _browse_models(db_session, 1)

        response = client.get(f"/api/v1/models?{query}", headers=auth_headers)

        assert response.status_code == 200, response.text
        expected = target if expected_slug == target.slug else other
        assert [row["id"] for row in response.json()] == [expected.id]

    def test_combines_values_within_a_filter_group_by_or_and_groups_by_and(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        target, _other = _browse_models(db_session, 1)

        response = client.get(
            "/api/v1/models?file_type=gcode&file_type=stl&storage=vault",
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()] == [target.id]

    def test_paginates_the_legacy_model_list_at_limit_and_offset_boundaries(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        _browse_models(db_session, 1)

        first = client.get("/api/v1/models?limit=1&offset=0", headers=auth_headers)
        second = client.get("/api/v1/models?limit=1&offset=1", headers=auth_headers)
        beyond = client.get("/api/v1/models?limit=1&offset=999", headers=auth_headers)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()[0]["id"] != second.json()[0]["id"]
        assert beyond.json() == []

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("limit=0", id="limit-below"),
            pytest.param("limit=501", id="limit-above"),
            pytest.param("offset=-1", id="negative-offset"),
            pytest.param("storage=remote", id="invalid-storage"),
            pytest.param("uploaded_after=not-a-date", id="invalid-date"),
        ],
    )
    def test_rejects_malformed_model_list_filters_and_pagination(
        self, client: TestClient, auth_headers: dict[str, str], query: str
    ) -> None:
        response = client.get(f"/api/v1/models?{query}", headers=auth_headers)

        assert response.status_code == 422, response.text

    def test_rejects_an_unauthenticated_model_list(self, client: TestClient) -> None:
        response = client.get("/api/v1/models")

        assert response.status_code == 401, response.text


class TestPageModels:
    def test_returns_a_globally_sorted_cursor_page(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        _browse_models(db_session, 1)

        response = client.get(
            "/api/v1/models/page?sort=name-asc&limit=1", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == 1
        assert response.json()["total"] == 2
        assert response.json()["next_cursor"]

    def test_resumes_a_cursor_page_without_gaps_or_duplicates(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        target, other = _browse_models(db_session, 1)
        first = client.get(
            "/api/v1/models/page?sort=name-asc&limit=1", headers=auth_headers
        )

        second = client.get(
            "/api/v1/models/page",
            params={
                "sort": "name-asc",
                "limit": 1,
                "cursor": first.json()["next_cursor"],
            },
            headers=auth_headers,
        )

        ids = [first.json()["items"][0]["id"], second.json()["items"][0]["id"]]
        assert set(ids) == {target.id, other.id}
        assert len(ids) == len(set(ids))
        assert second.json()["next_cursor"] is None

    def test_returns_a_terminal_cursor_page(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        _browse_models(db_session, 1)

        response = client.get(
            "/api/v1/models/page?sort=name-asc&limit=100", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["next_cursor"] is None

    def test_applies_a_canonical_filter_to_cursor_pages(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        target, _other = _browse_models(db_session, 1)

        response = client.get(
            "/api/v1/models/page?favorites=true", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["items"]] == [target.id]

    def test_rejects_an_unauthenticated_cursor_page(self, client: TestClient) -> None:
        response = client.get("/api/v1/models/page")

        assert response.status_code == 401, response.text

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("cursor=bad", id="cursor"),
            pytest.param("sort=bad", id="sort"),
            pytest.param("limit=0", id="limit"),
        ],
    )
    def test_rejects_malformed_sort_cursor_filter_and_page_limit(
        self, client, auth_headers, query
    ):
        response = client.get(f"/api/v1/models/page?{query}", headers=auth_headers)

        assert response.status_code in {400, 422}, response.text


class TestOutlinerModels:
    def test_lists_lightweight_outliner_leaves_without_a_filter(
        self, client, auth_headers, db_session
    ):
        target, other = _browse_models(db_session, 1)

        response = client.get("/api/v1/models/outliner", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert {row["id"] for row in response.json()} == {target.id, other.id}

    def test_applies_the_favorites_filter_to_outliner_leaves(
        self, client, auth_headers, db_session
    ):
        target, _other = _browse_models(db_session, 1)

        response = client.get(
            "/api/v1/models/outliner?favorites=true", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json() == [
            {
                "id": target.id,
                "name": target.name,
                "collection": "functional/brackets",
                "collection_id": target.collection_id,
            }
        ]

    def test_excludes_trashed_models_from_outliner_leaves(
        self, client, auth_headers, db_session
    ):
        live, trashed = _browse_models(db_session, 1)
        trashed.deleted_at = utcnow()
        db_session.add(trashed)
        db_session.commit()

        response = client.get("/api/v1/models/outliner", headers=auth_headers)

        ids = {row["id"] for row in response.json()}
        assert live.id in ids
        assert trashed.id not in ids

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param("limit=0", id="limit-below"),
            pytest.param("limit=501", id="limit-above"),
            pytest.param("storage=remote", id="storage"),
        ],
    )
    def test_rejects_malformed_outliner_filters_and_limit(
        self, client, auth_headers, query
    ):
        response = client.get(f"/api/v1/models/outliner?{query}", headers=auth_headers)

        assert response.status_code == 422, response.text


class TestModelFacets:
    def test_returns_unfiltered_facets_for_accessible_live_models(
        self, client, auth_headers, db_session
    ):
        _browse_models(db_session, 1)

        response = client.get("/api/v1/models/facets", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert {row["value"] for row in response.json()["file_type"]} == {
            "gcode",
            "stl",
        }

    def test_applies_a_text_filter_while_computing_facets(
        self, client, auth_headers, db_session
    ):
        _browse_models(db_session, 1)

        response = client.get("/api/v1/models/facets?q=Target", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["file_type"] == [{"value": "gcode", "count": 1}]
        assert response.json()["material_type"] == [{"value": "PLA", "count": 1}]
        assert response.json()["storage"] == [{"value": "vault", "count": 1}]

    def test_excludes_trashed_values_from_facets(
        self, client, auth_headers, db_session
    ):
        target, other = _browse_models(db_session, 1)
        other.deleted_at = utcnow()
        db_session.add(other)
        db_session.commit()

        response = client.get("/api/v1/models/facets", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert {row["value"] for row in response.json()["file_type"]} == {"gcode"}
        assert target.id is not None

    def test_rejects_malformed_facet_filters(self, client, auth_headers):
        response = client.get(
            "/api/v1/models/facets?storage=remote", headers=auth_headers
        )

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("", id="list"),
            pytest.param("page", id="page"),
            pytest.param("outliner", id="outliner"),
            pytest.param("facets", id="facets"),
        ],
    )
    def test_requires_superuser_for_printer_presence_filters(
        self, client, db_session, path
    ):
        user = _regular_user(db_session, f"browse-user-{path or 'list'}")
        headers = _headers_for(user)

        response = client.get(
            f"/api/v1/models/{path}?printer_presence=any", headers=headers
        )

        assert response.status_code == 403, response.text
