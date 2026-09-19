"""Measure actual search HTTP latency with local ONNX query inference.

The 32 frozen engineering texts/vectors are replicated to measure cardinality.
This is not an independent 100k-item quality corpus. Run on an otherwise idle
host, once per backend, retaining result.json even when the 300ms gate fails.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import resource
import secrets
import time
from contextlib import ExitStack
from contextvars import ContextVar
from pathlib import Path
from unittest.mock import patch


def environment(directory: Path, model: Path, backend: str):
    directory.mkdir(exist_ok=False)
    os.environ.update(
        {
            "OPENBLAS_NUM_THREADS": "1",
            "VAULT_DB_URL": f"sqlite:///{directory / 'vault.sqlite'}",
            "VAULT_SETUP_MODE": "trusted_network",
            "VAULT_SETUP_ALLOWED_HOSTS": "testserver",
            "VAULT_JWT_SECRET": secrets.token_hex(32),
            "VAULT_SECRETS_KEY": secrets.token_hex(32),
            "VAULT_SECRETS_KEY_FILE": str(directory / "secrets-key"),
            "VAULT_EMBEDDING_LOCAL_MODEL_DIR": str(model),
            "VAULT_EMBEDDING_ONNX_THREADS": "1",
            "VAULT_EMBEDDING_DOWNLOAD_ENABLED": "false",
            "VAULT_SEARCH_NATIVE_VECTORS_ENABLED": str(backend == "sqlite_vec").lower(),
        }
    )
    for key in (
        "DATA_DIR",
        "THUMB_DIR",
        "STAGING_DIR",
        "BACKUP_DIR",
        "ARTIFACT_CACHE_ROOT",
        "EMBEDDING_CACHE_DIR",
    ):
        target = directory / key.lower()
        target.mkdir()
        os.environ[f"VAULT_{key}"] = str(target)


def index_rows(session, generation_id, *, count):
    """Reject a scale report whose serving derivative lacks fixture vectors."""
    from sqlalchemy import func, table
    from sqlmodel import select

    from app.db.models import IndexGeneration, PassageVector
    from app.modules.search import vector_index

    generation = session.get(IndexGeneration, generation_id)
    assert generation is not None
    durable_count = session.exec(
        select(func.count(PassageVector.id)).where(
            PassageVector.generation_id == generation.id
        )
    ).one()
    assert durable_count == count, (durable_count, count)
    if generation.index_backend == "sqlite_vec":
        name = vector_index.table_name(generation.id, "sqlite")
        native_count = session.execute(
            select(func.count()).select_from(table(name))
        ).scalar_one()
        assert native_count == count, (native_count, count)
    return durable_count


def prepare_indexes(session, generation, *, count):
    """Finish derivatives after direct fixture inserts, before timing requests."""
    from app.modules.search import lexical_index, vector_index

    while lexical_index.rebuild_partition(session, limit=1024):
        pass
    assert lexical_index.capability(session) == "fts5"
    # Factory saves commit seed rows, so normal background repair can mark a
    # small derivative ready before bulk fixture insertion finishes. Those
    # direct fixture inserts intentionally bypass production publication.
    # Recreate the derivative explicitly, then prove its actual cardinality.
    vector_index.prepare(session, generation)
    if generation.index_backend == "sqlite_vec":
        from sqlalchemy import column, insert, table
        from sqlmodel import select

        from app.db.models import PassageVector

        assert generation.index_state == "building", generation.index_error
        # This is a query-cardinality fixture, not a backfill throughput run.
        # Its measured, full-dimension float blobs are copied unchanged into
        # the real extension table; production inference is measured separately.
        assert generation.quantization == "float32"
        name = vector_index.table_name(generation.id, "sqlite")
        native = table(name, column("rowid"), column("embedding"))
        session.execute(
            insert(native).from_select(
                ["rowid", "embedding"],
                select(PassageVector.id, PassageVector.vector_blob).where(
                    PassageVector.generation_id == generation.id,
                    PassageVector.native_dimension == generation.index_dimension,
                ),
            )
        )
        generation.index_state = "ready"
        session.add(generation)
        session.flush()
    else:
        while vector_index.rebuild_partition(session, generation, limit=1024):
            pass
    assert generation.index_state == "ready", generation.index_error
    return index_rows(session, generation.id, count=count)


def measure(directory: Path, *, count: int, backend: str, query_count: int):
    import numpy as np
    from fastapi.testclient import TestClient
    from printstash_core.inference.context import InferenceContext
    from printstash_core.search.passages import SearchSubject, SubjectType
    from sqlmodel import select

    from app.core.config import ensure_dirs, settings
    from app.db.migrate import run_migrations
    from app.db.models import SearchPassage
    from app.db.session import get_session_factory
    from app.main import app
    from app.modules.inference.configuration import embedding_provider
    from app.modules.inference.query import QueryRunner, close_queries
    from app.modules.search import (
        configuration,
        retrieval,
        vector_index,
        vector_store,
    )
    from app.modules.search.passages import sync_subject
    from app.schemas.inference import SearchSettings
    from tests.factories.library import build_model
    from tests.factories.search_scale import replicate_models
    from tests.factories.similarity import build_index_generation, build_passage_vector
    from tests.fakes.precomputed_embeddings import PrecomputedEmbeddings
    from tests.fakes.process_metrics import sample_processes
    from tests.paths import FIXTURES_DIR

    started, cpu_started = time.perf_counter(), time.process_time()
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
    corpus = json.loads((root / "corpus.json").read_text())
    with (root / "queries.csv").open() as stream:
        queries = [row for row in csv.DictReader(stream) if row["expected_id"]][
            :query_count
        ]
    if not len(corpus) <= count <= 100_000 or not 1 <= query_count <= 32:
        raise ValueError("search_scale_budget_invalid")
    ensure_dirs()
    run_migrations()
    sessions = get_session_factory()
    with TestClient(app) as client:
        client.headers["Origin"] = "http://testserver"
        preparation = client.post("/api/v1/setup/session")
        assert preparation.status_code == 200, preparation.text
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
        assert setup.status_code == 201, setup.text
        client.headers["Authorization"] = "Bearer " + setup.json()["access_token"]
        seeded = time.perf_counter()
        with sessions.scoped_session() as session:
            configuration.update(
                session, SearchSettings(enabled=True, local_models_enabled=True)
            )
            stored = vector_store.register_space(session, measured.space)
            generation = build_index_generation(session, stored, index_backend=backend)
            models = []
            for row in corpus:
                model = build_model(
                    session, row["name"], description=row["description"]
                )
                models.append(model)
                sync_subject(session, SearchSubject(SubjectType.MODEL, model.id))
                passage = session.exec(
                    select(SearchPassage).where(
                        SearchPassage.subject_type == "model",
                        SearchPassage.subject_id == model.id,
                    )
                ).one()
                build_passage_vector(
                    session,
                    generation,
                    passage=passage,
                    vector_blob=measured.blob(passage.text),
                )
            replicas = replicate_models(session, models, generation, count=count)
            indexed_count = prepare_indexes(session, generation, count=count)
            assert vector_index.serving_backend(generation) == backend
            generation_id = generation.id
            session.commit()
            provider = embedding_provider(session, measured.space)
        seed_seconds = time.perf_counter() - seeded
        # Report the real cold request separately; it may already be warm if the
        # normal background startup warmer won the race during fixture creation.
        before = time.perf_counter()
        cold = client.get(
            "/api/v1/search",
            params={
                "q": queries[0]["query"],
                "legs[]": ["lexical", "semantic_text"],
                "limit": 10,
            },
        )
        cold_seconds = time.perf_counter() - before
        assert cold.status_code == 200, cold.text
        warm_started = time.perf_counter()
        provider.validate(context=InferenceContext.bounded(120))
        warmup_seconds = time.perf_counter() - warm_started
        close_queries()
        components = ContextVar("search_scale_components", default=None)

        def timed(original, label):
            def run(*args, **kwargs):
                before = time.perf_counter()
                try:
                    return original(*args, **kwargs)
                finally:
                    current = components.get()
                    if current is not None:
                        current[label] = (
                            current.get(label, 0) + time.perf_counter() - before
                        )

            return run

        observations = []
        processes = {}
        # Pass-through timing only: every production function and ONNX worker runs.
        with ExitStack() as stack:
            for owner, name, label in (
                (QueryRunner, "embed", "embedding_seconds"),
                (vector_store, "query", "vector_seconds"),
                (retrieval, "fuse", "fusion_seconds"),
                (retrieval, "read_items_by_ids", "model_materialization_seconds"),
            ):
                stack.enter_context(
                    patch.object(owner, name, timed(getattr(owner, name), label))
                )
            for query in queries:
                parts = {}
                token = components.set(parts)
                before = time.perf_counter()
                try:
                    response = client.get(
                        "/api/v1/search",
                        params={
                            "q": query["query"],
                            "legs[]": ["lexical", "semantic_text"],
                            "limit": 10,
                        },
                    )
                finally:
                    total = time.perf_counter() - before
                    components.reset(token)
                assert response.status_code == 200, response.text
                body = response.json()
                assert body["leg_errors"] == {}, body
                assert body["lexical_backend"] == "fts5"
                assert parts.get("embedding_seconds", 0) > 0
                assert parts.get("vector_seconds", 0) > 0
                sample_processes(processes)
                expected_seed = replicas["seed_ids"][int(query["expected_id"]) - 1]

                def seed_for(id):
                    if id < replicas["first_replica_id"]:
                        return id
                    return replicas["seed_ids"][
                        (id - replicas["first_replica_id"]) % len(corpus)
                    ]

                observations.append(
                    {
                        "query_id": query["query_id"],
                        "seconds": total,
                        "components": parts,
                        "items": len(body["items"]),
                        "expected_text_in_top10": any(
                            seed_for(item["subject_id"]) == expected_seed
                            for item in body["items"]
                        ),
                    }
                )
        # Retain actual timings even if final cardinality validation fails.
        (directory / "observations.json").write_text(
            json.dumps(observations, indent=2) + "\n"
        )
        with sessions.scoped_session() as session:
            final_indexed_count = index_rows(session, generation_id, count=count)
        durations = [row["seconds"] for row in observations]
        p95 = float(np.percentile(durations, 95))
        result = {
            "architecture": platform.machine(),
            "platform": platform.platform(),
            "cpu_affinity": sorted(os.sched_getaffinity(0)),
            "passages": count,
            "distinct_texts": len(corpus),
            "verified_index_rows_before_queries": indexed_count,
            "verified_index_rows_after_queries": final_indexed_count,
            "corpus_sha256": measured.payload["corpus_sha256"],
            "queries_sha256": measured.payload["queries_sha256"],
            "query_count": len(queries),
            "space": measured.space.__dict__,
            "backend": backend,
            "onnx_threads": 1,
            "query_vectors_cached": False,
            "seed_seconds": seed_seconds,
            "cold_request_seconds": cold_seconds,
            "cold_leg_errors": cold.json()["leg_errors"],
            "warmup_after_cold_seconds": warmup_seconds,
            "p50_seconds": float(np.percentile(durations, 50)),
            "p95_seconds": p95,
            "budget_seconds": 0.300,
            "budget_passed": p95 < 0.300,
            "observations": observations,
            "processes": processes,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "child_peak_rss_kib": resource.getrusage(
                resource.RUSAGE_CHILDREN
            ).ru_maxrss,
            "cpu_seconds": time.process_time() - cpu_started,
            "wall_seconds": time.perf_counter() - started,
        }
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100_000)
    parser.add_argument("--queries", type=int, default=32)
    parser.add_argument("--backend", choices=("numpy", "sqlite_vec"), default="numpy")
    options = parser.parse_args()
    environment(options.directory, options.model_directory, options.backend)
    result = measure(
        options.directory,
        count=options.count,
        backend=options.backend,
        query_count=options.queries,
    )
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["budget_passed"] else 1)


if __name__ == "__main__":
    main()
