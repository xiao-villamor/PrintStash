"""Explicit real-model measurement; provision the pinned files before running."""

import csv
import hashlib
import json
import os
import resource
import statistics
import time
from pathlib import Path

from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import text
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import SearchExpansion, SearchExpansionTerm, SearchPassage
from app.db.session import get_session_factory
from app.modules.inference.model_cache import inspect
from app.modules.inference.worker_pool import pool
from app.modules.search import configuration, lexical_index
from app.modules.search.expansion_worker import ExpansionProcessor
from app.modules.search.passages import sync_subject
from app.modules.search.retrieval import search
from app.schemas.inference import SearchSettings
from tests.paths import FIXTURES_DIR


class TestPreplacedSparse:
    def test_measures_the_pinned_sparse_profile(
        self, db_session, make_user, make_model, monkeypatch, tmp_path
    ):
        directory = Path(os.environ["AI_SEARCH_SPARSE_ASSETS"])
        monkeypatch.setitem(_overlay, "embedding_local_model_dir", str(directory))
        monkeypatch.setitem(_overlay, "embedding_cache_dir", tmp_path / "cache")
        monkeypatch.setitem(_overlay, "embedding_onnx_threads", 1)
        model = inspect(directory)
        root = FIXTURES_DIR / "search"
        corpus = json.loads((root / "corpus.json").read_text())
        with (root / "queries.csv").open() as stream:
            queries = list(csv.DictReader(stream))
        actor = make_user(superuser=True)
        subjects = {}
        for item in corpus:
            subject = make_model(item["name"], description=item["description"])
            subjects[item["id"]] = subject.id
            sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        lexical_index.rebuild_partition(db_session)
        configuration.update(
            db_session,
            SearchSettings(enabled=True, local_models_enabled=True),
            actor_id=actor.id,
        )
        db_session.commit()
        ordinary = {}
        for item in queries:
            ordinary[item["query_id"]] = [
                hit.subject_id
                for hit in search(
                    db_session, actor, item["query"], mode="lexical", limit=5
                ).items
            ]
        db_session.commit()
        original_bytes = db_session.exec(
            text(
                "SELECT sum(pgsize) FROM dbstat WHERE name IN (SELECT name FROM sqlite_schema WHERE tbl_name IN ('search_passages','search_lexical_postings','search_lexical_terms','search_lexical_state') OR tbl_name LIKE 'search_passages_fts%')"
            )
        ).one()[0]
        configuration.update(
            db_session,
            SearchSettings(
                enabled=True,
                local_models_enabled=True,
                sparse_expansion_enabled=True,
                sparse_model_id=model.id,
            ),
            actor_id=actor.id,
        )
        db_session.commit()
        processor = ExpansionProcessor(get_session_factory())
        timings = []
        for _ in range(len(corpus) + 2):
            start = time.monotonic()
            if not processor.work_one():
                break
            timings.append(time.monotonic() - start)
        db_session.expire_all()
        work = db_session.exec(select(SearchExpansion)).all()
        assert len(work) == len(corpus)
        assert all(row.phase == "ready" for row in work), [
            (row.phase, row.error_code) for row in work
        ]
        expanded = {}
        for item in queries:
            expanded[item["query_id"]] = [
                hit.subject_id
                for hit in search(
                    db_session, actor, item["query"], mode="lexical", limit=5
                ).items
            ]
        known = [item for item in queries if item["expected_id"]]

        def recall(results):
            return sum(
                subjects[int(item["expected_id"])] in results[item["query_id"]]
                for item in known
            ) / len(known)

        terms = db_session.exec(select(SearchExpansionTerm)).all()
        sparse_bytes = db_session.exec(
            text(
                "SELECT sum(pgsize) FROM dbstat WHERE name IN (SELECT name FROM sqlite_schema WHERE tbl_name IN ('search_expansions','search_expansion_terms'))"
            )
        ).one()[0]
        report = {
            "model_id": model.id,
            "corpus_sha256": hashlib.sha256(
                (root / "corpus.json").read_bytes()
            ).hexdigest(),
            "queries_sha256": hashlib.sha256(
                (root / "queries.csv").read_bytes()
            ).hexdigest(),
            "threads": 1,
            "passages": len(work),
            "terms": len(terms),
            "max_terms_per_passage": max(
                sum(term.passage_id == row.passage_id for term in terms) for row in work
            ),
            "original_index_bytes": original_bytes,
            "expansion_bytes": sparse_bytes,
            "combined_ratio": (original_bytes + sparse_bytes) / original_bytes,
            "cold_first_document_seconds": timings[0],
            "warm_document_median_seconds": statistics.median(timings[1:]),
            "backfill_seconds": sum(timings),
            "recall_at_5_original": recall(ordinary),
            "recall_at_5_expanded": recall(expanded),
            "improved": [
                item["query_id"]
                for item in known
                if subjects[int(item["expected_id"])] not in ordinary[item["query_id"]]
                and subjects[int(item["expected_id"])] in expanded[item["query_id"]]
            ],
            "regressed": [
                item["query_id"]
                for item in known
                if subjects[int(item["expected_id"])] in ordinary[item["query_id"]]
                and subjects[int(item["expected_id"])] not in expanded[item["query_id"]]
            ],
            "outside_domain_results": {
                item["query_id"]: len(expanded[item["query_id"]])
                for item in queries
                if not item["expected_id"]
            },
        }
        passages = {row.id: row for row in db_session.exec(select(SearchPassage)).all()}
        frozen = {
            "model_id": model.id,
            "corpus_sha256": report["corpus_sha256"],
            "queries_sha256": report["queries_sha256"],
            "documents": {
                hashlib.sha256(passage.text.encode()).hexdigest(): {
                    term.term: term.weight
                    for term in terms
                    if term.passage_id == passage.id
                }
                for passage in passages.values()
            },
        }
        if os.environ.get("AI_SEARCH_SPARSE_TERMS"):
            Path(os.environ["AI_SEARCH_SPARSE_TERMS"]).write_text(
                json.dumps(frozen, indent=2, sort_keys=True) + "\n"
            )
        pool.close()
        report["peak_child_rss_kib"] = resource.getrusage(
            resource.RUSAGE_CHILDREN
        ).ru_maxrss
        Path(
            os.environ.get(
                "AI_SEARCH_SPARSE_REPORT", str(tmp_path / "measurement.json")
            )
        ).write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, sort_keys=True))
        assert report["max_terms_per_passage"] <= 64
