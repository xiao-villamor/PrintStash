"""API content writes persist intent; the projection worker publishes passages."""

import pytest
from sqlmodel import select

from app.db.models.search import SearchPassage
from app.db.projections import bind_content_projection
from app.db.session import get_session_factory
from app.modules.search.projection import LibraryProjection
from tests.ingestion_work import drain_sources
from tests.search_projection import drain_search


@pytest.fixture
def projection():
    previous = bind_content_projection(LibraryProjection())
    yield
    bind_content_projection(previous)


class TestSearchPassageLifecycle:
    @pytest.mark.asyncio
    async def test_indexes_a_new_document_after_background_projection(
        self, projection, api, superuser_headers
    ):
        response = await api.post(
            "/api/v1/documents",
            headers=superuser_headers,
            json={"name": "Assembly guide", "body": "Slide the lid into the box."},
        )
        assert response.status_code == 201, response.text

        with get_session_factory().scoped_session() as session:
            assert session.exec(select(SearchPassage.text)).all() == []
            drain_search(session)
            assert session.exec(select(SearchPassage.text)).all() == [
                "Title: Assembly guide\nBody: Slide the lid into the box."
            ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fts_available", [True, False], ids=["fts5", "lost-fts5"])
    async def test_searches_all_public_subject_types(
        self, projection, api, superuser_headers, fts_available
    ):
        import asyncio

        from sqlalchemy import text

        from app.modules.search.lexical_index import rebuild_partition
        from tests.paths import FIXTURES_DIR

        collection = await api.post(
            "/api/v1/collections", headers=superuser_headers, json={"name": "Assembly"}
        )
        assert collection.status_code == 201, collection.text
        document = await api.post(
            "/api/v1/documents",
            headers=superuser_headers,
            json={"name": "Assembly guide", "body": "Fit the lid"},
        )
        assert document.status_code == 201, document.text
        aggregate = await api.post(
            "/api/v1/multipart-models",
            headers=superuser_headers,
            json={"name": "Assembly kit"},
        )
        assert aggregate.status_code == 201, aggregate.text
        with get_session_factory().scoped_session() as session:
            rebuild_partition(session)
            session.commit()
            if not fts_available:
                session.execute(text("DROP TABLE search_passages_fts"))
                session.commit()
        source = FIXTURES_DIR / "real_orca_ender3_benchy.gcode"
        upload = await api.post(
            "/api/v1/ingest/orca",
            headers=superuser_headers,
            files={"file": (source.name, source.read_bytes(), "text/plain")},
            data={"model_name": "Assembly bracket"},
        )
        assert upload.status_code == 202, upload.text
        for _ in range(100):
            await drain_sources()
            response = await api.get(
                f"/api/v1/ingest/jobs/{upload.json()['job_id']}",
                headers=superuser_headers,
            )
            job = response.json()
            if job["state"] in {"completed", "failed", "duplicate"}:
                break
            await asyncio.sleep(0.05)
        assert job["state"] == "completed", job
        with get_session_factory().scoped_session() as session:
            drain_search(session)
        response = await api.get(
            "/api/v1/search",
            headers=superuser_headers,
            params={"q": "assembly", "mode": "lexical"},
        )

        assert response.status_code == 200, response.text
        assert {row["subject_type"] for row in response.json()["items"]} == {
            "model",
            "collection",
            "multipart_model",
            "document",
        }
        assert response.json()["lexical_backend"] == (
            "fts5" if fts_available else "ranked_like"
        )
