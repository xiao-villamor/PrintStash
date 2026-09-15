"""An installed consumer of the public inference/vector seams, without Similar Models."""

import os
import sys
import time
from importlib.util import find_spec

from fastapi.testclient import TestClient
from printstash_core.inference import EmbeddingInput
from printstash_core.search.passages import SubjectType
from sqlalchemy import event, literal
from sqlmodel import select

from app.core.config import ensure_dirs, settings
from app.db.migrate import run_migrations
from app.db.models import PassageVector, SearchPassage, User
from app.db.session import get_session_factory
from app.main import app
from app.modules.inference import model_cache
from app.modules.inference.local import configured_provider
from app.modules.search import vector_store
from app.modules.search.access import visible_passage_ids
from app.runtime.search import process_one
from tests.paths import FIXTURES_DIR


def exercise_search(client):
    """All four Subjects enter through public writes, then survive a real rebuild."""
    expected = {kind.value for kind in SubjectType}
    for path, payload in (
        ("collections", {"name": "red assembly"}),
        ("multipart-models", {"name": "red assembly kit"}),
    ):
        response = client.post(f"/api/v1/{path}", json=payload)
        assert response.status_code == 201, response.text
    source = FIXTURES_DIR / "real_orca_ender3_benchy.gcode"
    upload = client.post(
        "/api/v1/ingest/orca",
        files={"file": (source.name, source.read_bytes(), "text/plain")},
        data={"model_name": "red assembly bracket"},
    )
    assert upload.status_code == 202, upload.text
    for _ in range(100):
        job = client.get(f"/api/v1/ingest/jobs/{upload.json()['job_id']}").json()
        if job["state"] in {"completed", "failed", "duplicate"}:
            break
        time.sleep(0.05)
    assert job["state"] == "completed", job
    response = client.get("/api/v1/search", params={"q": "red", "mode": "lexical"})
    assert response.status_code == 200, response.text
    assert {row["subject_type"] for row in response.json()["items"]} == expected
    assert all(
        row.get("model", {}).get("family") is None
        for row in response.json()["items"]
        if row.get("model")
    )
    response = client.patch(
        "/api/v1/search/settings", json={"enabled": True, "local_models_enabled": True}
    )
    assert response.status_code == 200, response.text
    model = model_cache.inspect(settings.embedding_cache_dir / "text")
    active = None
    for quantization in ("float32", "int8"):
        proposal = client.post(
            "/api/v1/search/generations",
            json={
                "local_model_id": model.id,
                "index_backend": "numpy",
                "quantization": quantization,
            },
        )
        assert proposal.status_code == 202, proposal.text
        proposed = proposal.json()["id"]
        for step in range(80):
            if active is not None:
                serving = client.get(
                    "/api/v1/search", params={"q": "red", "legs[]": "semantic_text"}
                )
                assert serving.status_code == 200, serving.text
                assert serving.json()["generations"] in ([active], [proposed])
                assert {
                    row["subject_type"] for row in serving.json()["items"]
                } == expected
            process_one(tuple(SubjectType)[step % len(SubjectType)])
            generations = client.get("/api/v1/search/generations").json()
            generation = next(row for row in generations if row["id"] == proposed)
            if generation["state"] == "active":
                break
        assert generation["state"] == "active", generation
        assert generation["quantization"] == quantization
        result = client.get(
            "/api/v1/search", params={"q": "red", "legs[]": "semantic_text"}
        )
        assert result.status_code == 200, result.text
        assert result.json()["generations"] == [proposed]
        assert result.json()["leg_errors"] == {}
        assert {row["subject_type"] for row in result.json()["items"]} == expected
        assert all(
            any(evidence["leg"] == "semantic_text" for evidence in row["evidence"])
            for row in result.json()["items"]
        )
        if active is not None:
            assert (
                next(row for row in generations if row["id"] == active)["state"]
                == "retired"
            )
        active = proposed


def run():
    assert find_spec("app.modules.similarity") is None
    families_removed = "families" in os.environ.get("TEST_REMOVED_FEATURES", "").split(
        ","
    )
    if families_removed:
        assert find_spec("app.modules.library.families") is None
    ensure_dirs()
    run_migrations()
    with TestClient(app) as client:
        assert app.state.similarity_task is None
        client.headers["Origin"] = "http://testserver"
        preparation = client.post("/api/v1/setup/session")
        assert preparation.status_code == 200
        client.headers["X-PrintStash-Setup-CSRF"] = preparation.json()["csrf"]
        setup = client.post(
            "/api/v1/setup",
            json={
                "username": "owner",
                "password": "Password123",
                "storage_backend": "local",
                "data_dir": str(settings.data_dir),
                "thumb_dir": str(settings.thumb_dir),
            },
        )
        assert setup.status_code == 201
        client.headers["Authorization"] = "Bearer " + setup.json()["access_token"]
        response = client.post(
            "/api/v1/documents",
            json={"name": "Independent guide", "body": "A red boat"},
        )
        assert response.status_code == 201
        doc_id = response.json()["id"]
        lexical = client.get("/api/v1/search", params={"q": "boat", "mode": "lexical"})
        assert lexical.status_code == 200
        assert any(item["subject_id"] == doc_id for item in lexical.json()["items"])
        sessions = get_session_factory()
        provider = configured_provider(sessions)
        generation_id = vector_store.initialize(sessions, provider)
        vector = provider.embed((EmbeddingInput("text", text="red"),), provider.space)[
            0
        ]
        with sessions.scoped_session() as session:
            actor = session.exec(select(User).where(User.username == "owner")).one()
            passage = session.exec(
                select(SearchPassage).where(
                    SearchPassage.subject_type == "document",
                    SearchPassage.subject_id == doc_id,
                )
            ).one()
            source = select(
                SearchPassage.subject_type,
                SearchPassage.subject_id,
                literal(None).label("model_id"),
                literal(None).label("file_id"),
                SearchPassage.id.label("passage_id"),
            ).where(
                SearchPassage.id == passage.id,
                SearchPassage.content_hash == passage.content_hash,
                SearchPassage.id.in_(visible_passage_ids(session, actor)),
            )
            assert vector_store.publish(
                session,
                generation_id=generation_id,
                space=provider.space,
                unit_kind="independent_passage",
                unit_key=f"independent:{passage.id}",
                input_hash=passage.content_hash,
                vector=vector,
                source=source,
            )
            session.commit()
            original = session.exec(select(PassageVector)).one()
            before = (original.id, original.vector_blob, provider.space.config_hash)
            # The external ranking algorithm is consumer metadata. Changing it
            # does not alter an immutable inference Space or republish native bytes.
            observations = []
            for algorithm_version in ("consumer-ranker-v1", "consumer-ranker-v2"):
                result = vector_store.query(
                    session,
                    generation_id=generation_id,
                    space=provider.space,
                    vector=vector,
                    allowed_ids=select(PassageVector.id).where(
                        PassageVector.passage_id.in_(
                            visible_passage_ids(session, actor)
                        )
                    ),
                )
                observations.append((algorithm_version, result.items[0].subject_id))
            assert observations == [
                ("consumer-ranker-v1", doc_id),
                ("consumer-ranker-v2", doc_id),
            ]
            same = vector_store.register_space(session, provider.space)
            assert same.config_hash == before[2]
            assert (original.id, original.vector_blob) == before[:2]
        family_statements = []

        def observe(_conn, _cursor, statement, _parameters, _context, _many):
            if families_removed and "model_famil" in statement.lower():
                family_statements.append(statement.split()[0])

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", observe)
        try:
            exercise_search(client)
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        assert family_statements == []
        if families_removed:
            assert client.get("/api/v1/families").status_code == 404
            assert not any(
                name.startswith("app.modules.library.families") for name in sys.modules
            )
        assert not any(
            name == "app.modules.similarity"
            or name.startswith("app.modules.similarity.")
            for name in sys.modules
        )
    print("independent-search-consumer-complete")


if __name__ == "__main__":
    run()
