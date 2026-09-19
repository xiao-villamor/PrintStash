"""Real BGE vector fixtures evaluate the actual authorized lexical/hybrid readers."""

import csv
import hashlib
import json

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlmodel import select

from app.db.models import SearchPassage
from app.modules.inference.query import close_queries
from app.modules.search import configuration, lexical_index, semantic
from app.modules.search.access import visible_passage_ids
from app.modules.search.lexical_query import ordered_passages
from app.modules.search.passages import sync_subject
from app.modules.search.retrieval import search
from app.schemas.inference import SearchSettings
from tests.fakes.precomputed_embeddings import PrecomputedEmbeddings
from tests.paths import FIXTURES_DIR


@pytest.fixture
def quality_library(
    db_session,
    make_model,
    make_user,
    make_embedding_space,
    make_index_generation,
    make_passage_vector,
    monkeypatch,
):
    root = FIXTURES_DIR / "search"
    measured = PrecomputedEmbeddings(root / "bge-small-en-v1.5-vectors.json")
    assert (
        measured.payload["corpus_sha256"]
        == hashlib.sha256((root / "corpus.json").read_bytes()).hexdigest()
    )
    assert (
        measured.payload["queries_sha256"]
        == hashlib.sha256((root / "queries.csv").read_bytes()).hexdigest()
    )
    actor = make_user(superuser=True)
    space = measured.space
    stored = make_embedding_space(
        config_hash=space.config_hash,
        config_json=json.dumps(space.__dict__),
        model_key=space.model_key,
        model_revision=space.model_revision,
        native_dimension=space.dimension,
        modality="text",
        profile="semantic_text",
    )
    generation = make_index_generation(stored)
    subjects = {}
    for row in json.loads((root / "corpus.json").read_text()):
        model = make_model(row["name"], description=row["description"])
        subjects[row["id"]] = model.id
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, model.id))
        passage = db_session.exec(
            select(SearchPassage).where(
                SearchPassage.subject_type == "model",
                SearchPassage.subject_id == model.id,
            )
        ).one()
        make_passage_vector(
            generation, passage=passage, vector_blob=measured.blob(passage.text)
        )
    configuration.update(db_session, SearchSettings(enabled=True))
    lexical_index.rebuild_partition(db_session)
    db_session.commit()
    monkeypatch.setattr(semantic, "embedding_provider", lambda *args: measured)
    close_queries()
    with (root / "queries.csv").open() as stream:
        queries = list(csv.DictReader(stream))
    yield actor, queries, subjects
    close_queries()


class TestTextQuality:
    def test_measures_real_text_retrieval_quality(self, db_session, quality_library):
        actor, queries, subjects = quality_library
        counts = {"hybrid": 0, "bm25": 0, "ilike": 0}
        total = 0
        failures = []
        outside = {}
        for row in queries:
            if not row["expected_id"]:
                result = search(db_session, actor, row["query"], limit=5)
                outside[row["query_id"]] = {
                    "returned": len(result.items),
                    "outcome": result.outcome,
                }
                continue
            expected = subjects[int(row["expected_id"])]
            total += 1
            hybrid = search(db_session, actor, row["query"], limit=5)
            lexical = search(db_session, actor, row["query"], mode="lexical", limit=5)
            like = db_session.exec(
                ordered_passages(
                    db_session,
                    row["query"],
                    visible_passage_ids(db_session, actor),
                    limit=5,
                    force_like=True,
                )
            ).all()
            like_subjects = {
                db_session.get(SearchPassage, id).subject_id for id, _ in like
            }
            assert hybrid.leg_errors == {}, hybrid
            assert lexical.lexical_backend == "fts5"
            counts["hybrid"] += expected in {item.subject_id for item in hybrid.items}
            counts["bm25"] += expected in {item.subject_id for item in lexical.items}
            counts["ilike"] += expected in like_subjects
            if expected not in {item.subject_id for item in hybrid.items}:
                failures.append(row["query_id"])
        metrics = {name: value / total for name, value in counts.items()}
        print(
            json.dumps(
                {
                    "recall_at_5": metrics,
                    "queries": total,
                    "missed_queries": failures,
                    "out_of_domain": outside,
                }
            )
        )
        assert metrics["hybrid"] >= 0.9
        assert metrics["bm25"] >= 0.6
