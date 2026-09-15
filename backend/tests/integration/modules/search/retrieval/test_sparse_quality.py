"""Frozen real SPLADE weights evaluate the original and expanded lexical readers."""

import csv
import hashlib
import json

from printstash_core.search.passages import SearchSubject, SubjectType
from sqlmodel import select

from app.db.models import SearchPassage
from app.modules.search import configuration, lexical_index
from app.modules.search.passages import sync_subject
from app.modules.search.retrieval import search
from app.schemas.inference import SearchSettings
from tests.paths import FIXTURES_DIR


class TestSparseQuality:
    def test_measures_frozen_real_sparse_expansion(
        self,
        db_session,
        make_model,
        make_user,
        make_system_config,
        make_search_expansion,
        make_search_expansion_term,
    ):
        root = FIXTURES_DIR / "search"
        measured = json.loads((root / "splade-pp-en-v1-terms.json").read_text())
        for name, key in (
            ("corpus.json", "corpus_sha256"),
            ("queries.csv", "queries_sha256"),
        ):
            assert (
                measured[key] == hashlib.sha256((root / name).read_bytes()).hexdigest()
            )
        actor = make_user(superuser=True)
        subjects = {}
        for item in json.loads((root / "corpus.json").read_text()):
            model = make_model(item["name"], description=item["description"])
            subjects[item["id"]] = model.id
            sync_subject(db_session, SearchSubject(SubjectType.MODEL, model.id))
            passage = db_session.exec(
                select(SearchPassage).where(
                    SearchPassage.subject_id == model.id,
                    SearchPassage.subject_type == "model",
                )
            ).one()
            expansion = make_search_expansion(passage, recipe=measured["model_id"])
            weights = measured["documents"][
                hashlib.sha256(passage.text.encode()).hexdigest()
            ]
            assert 0 < len(weights) <= 64
            for term, weight in weights.items():
                make_search_expansion_term(expansion, term, weight=weight)
        lexical_index.rebuild_partition(db_session)
        with (root / "queries.csv").open() as stream:
            queries = list(csv.DictReader(stream))
        config_row = make_system_config()
        recalls = []
        for enabled in (False, True):
            # A frozen measurement needs no installed model or inference path.
            config_row.ai_search_settings_json = SearchSettings(
                enabled=True,
                local_models_enabled=True,
                sparse_expansion_enabled=enabled,
                sparse_model_id=measured["model_id"],
            ).model_dump_json()
            db_session.add(config_row)
            db_session.commit()
            hits = 0
            outside = {}
            for item in queries:
                results = search(
                    db_session, actor, item["query"], mode="lexical", limit=5
                ).items
                if item["expected_id"]:
                    hits += subjects[int(item["expected_id"])] in [
                        hit.subject_id for hit in results
                    ]
                else:
                    outside[item["query_id"]] = len(results)
            recalls.append(hits / 32)
            print(
                {
                    "expanded": enabled,
                    "recall_at_5": recalls[-1],
                    "outside_domain": outside,
                }
            )
        assert recalls == [0.875, 0.875]
        configuration.update(db_session, SearchSettings())
