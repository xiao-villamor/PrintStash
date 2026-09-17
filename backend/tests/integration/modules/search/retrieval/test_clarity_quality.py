"""Frozen independent acceptance cases replay measured BGE and CLIP embeddings.

No private library content or invented embeddings. Empty metadata on the goose
makes its appearance the only useful retrieval signal.
"""

import base64
import hashlib
import json
import struct
import time

import pytest
from printstash_core.inference import EmbeddingSpace
from printstash_core.search.passages import SearchSubject, SubjectType
from printstash_core.search.visual_inputs import VisualRecipe
from sqlmodel import select

from app.db.models import FileType, SearchPassage
from app.modules.inference.query import close_queries
from app.modules.search import (
    configuration,
    lexical_index,
    semantic,
    vector_store,
    visual_query,
)
from app.modules.search.passages import sync_subject
from app.modules.search.retrieval import search
from app.schemas.inference import SearchSettings
from tests.paths import FIXTURES_DIR, REPO_ROOT


class MeasuredProvider:
    def __init__(self, data):
        self.data = data

    def embed(self, inputs, space, *, context=None):
        return tuple(
            struct.unpack(
                f"<{space.dimension}f",
                base64.b64decode(
                    self.data["vectors"][hashlib.sha256(item.text.encode()).hexdigest()]
                ),
            )
            for item in inputs
        )


@pytest.fixture
def clarity_library(
    db_session,
    make_user,
    make_model,
    make_file,
    make_index_generation,
    make_passage_vector,
    monkeypatch,
):
    root = FIXTURES_DIR / "search"
    corpus = json.loads((root / "clarity-corpus.json").read_text())
    measured = json.loads((root / "clarity-vectors.json").read_text())
    assert (
        measured["corpus_sha256"]
        == hashlib.sha256((root / "clarity-corpus.json").read_bytes()).hexdigest()
    )
    assert (
        measured["generator_sha256"]
        == hashlib.sha256(
            (REPO_ROOT / "backend/tests/fakes/search_clarity_geometry.py").read_bytes()
        ).hexdigest()
    )
    actor = make_user(superuser=True)
    spaces = {
        "text": EmbeddingSpace(**measured["text"]["space"]),
        "clip": VisualRecipe.space(
            EmbeddingSpace(**measured["clip"]["space"]),
            image_size=224,
            profile="thumbnail",
        ),
    }
    generations = {
        key: make_index_generation(
            vector_store.register_space(db_session, space),
            index_backend="numpy",
            effective_backend="numpy",
            index_dimension=space.dimension,
        )
        for key, space in spaces.items()
    }
    ids = {}
    for row in corpus["calibration"] + corpus["validation"]:
        model = make_model(row["name"], description=row["description"])
        ids[row["id"]] = model.id
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, model.id))
        passage = db_session.exec(
            select(SearchPassage).where(
                SearchPassage.subject_id == model.id,
                SearchPassage.subject_type == "model",
            )
        ).one()
        blob = base64.b64decode(
            measured["text"]["vectors"][
                hashlib.sha256(passage.text.encode()).hexdigest()
            ]
        )
        make_passage_vector(generations["text"], passage=passage, vector_blob=blob)
        file = make_file(model, file_type=FileType.STL)
        make_passage_vector(
            generations["clip"],
            file,
            unit_kind="visual_mean",
            unit_key=f"file:{file.id}:mean",
            vector_blob=base64.b64decode(
                measured["clip"]["images"][row["id"]][0]["vector"]
            ),
        )
    configuration.update(db_session, SearchSettings(enabled=True))
    lexical_index.rebuild_partition(db_session)
    db_session.commit()
    monkeypatch.setattr(
        semantic, "embedding_provider", lambda *args: MeasuredProvider(measured["text"])
    )
    monkeypatch.setattr(
        visual_query,
        "embedding_provider",
        lambda *args: MeasuredProvider(measured["clip"]),
    )
    close_queries()
    yield actor, ids
    close_queries()


class TestClarityQuality:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("specter", "spectre"),
            ("spectre", "spectre"),
            ("goose", "goose"),
            ("holder", "holder"),
        ],
    )
    def test_returns_relevant_objects_without_unrelated_fillers(
        self, db_session, clarity_library, query, expected
    ):
        actor, ids = clarity_library
        started = time.monotonic()
        result = search(db_session, actor, query, limit=5)
        returned = [item.subject_id for item in result.items]
        print(
            json.dumps(
                {
                    "query": query,
                    "returned": returned,
                    "relevant": ids[expected],
                    "milliseconds": (time.monotonic() - started) * 1000,
                    "errors": result.leg_errors,
                }
            )
        )
        assert result.leg_errors == {}
        assert returned == [ids[expected]]

    def test_respects_explicit_administrator_floors(self, db_session, clarity_library):
        actor, ids = clarity_library
        settings = configuration.settings(db_session)
        floors = {
            leg.space.config_hash: -1 for leg in semantic.registry(db_session, settings)
        }
        configuration.update(
            db_session, SearchSettings(enabled=True, semantic_floors=floors)
        )
        db_session.commit()
        result = search(db_session, actor, "giraffe")
        assert result.leg_errors == {}
        assert result.items
        assert any(item.subject_id == ids["goose"] for item in result.items)

    @pytest.mark.parametrize("query", ["giraffe", "helicopter"])
    def test_abstains_for_absent_concepts(self, db_session, clarity_library, query):
        actor, _ = clarity_library
        result = search(db_session, actor, query)
        assert result.leg_errors == {}
        assert result.items == []
        assert result.outcome == "no_strong_matches"
