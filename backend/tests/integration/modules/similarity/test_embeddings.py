"""One existing run resumes native embedding units without withholding geometric review."""

import hashlib
import io
import json

import pytest
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import FileType, PassageVector, SimilarityCandidate
from app.db.session import get_session_factory
from app.modules.inference.search import SearchRequest, capabilities, search
from app.modules.similarity import configuration, runs
from app.modules.similarity.processing import SimilarityProcessor
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories.embeddings import local_embedding_assets
from tests.factories.geometry import tetrahedron


class TestEmbeddingRun:
    def test_resumes_vectors_before_geometric_verification(
        self,
        db_session,
        make_user,
        make_model,
        make_file,
        tmp_path,
        monkeypatch,
        local_storage,
    ):
        directory = local_embedding_assets(tmp_path / "assets")
        monkeypatch.setitem(_overlay, "embedding_local_model_dir", str(directory))
        monkeypatch.setitem(_overlay, "embedding_model_key", "two-tower-contract")
        actor = make_user(superuser=True)
        configuration.update_settings(
            db_session,
            actor,
            {"enabled": True, "embeddings_enabled": True, "sample_points": 256},
        )
        backend = get_backend()
        content = tetrahedron().export(file_type="stl")
        for _ in range(2):
            file = make_file(
                make_model(),
                file_type=FileType.STL,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
            file.path = backend.blob_key(
                file.model.slug, file.version, file.original_filename
            )
            db_session.add(file)
            db_session.commit()
            backend.write_stream(io.BytesIO(content), file.path)
        run = runs.start(db_session, actor)
        for _ in range(60):
            SimilarityProcessor(get_session_factory(), backend).work_one()
            db_session.refresh(run)
            if run.state in runs.TERMINAL:
                break
        assert run.state == "completed", (
            run.failure_code,
            run.checkpoint_json,
            run.counters_json,
        )
        assert json.loads(run.counters_json)["embedded"] == 4
        assert len(db_session.exec(select(PassageVector)).all()) == 4
        assert len(db_session.exec(select(SimilarityCandidate)).all()) == 1
        assert capabilities(db_session, get_session_factory())["text_to_shape"] is True
        response = search(
            db_session, get_session_factory(), actor, SearchRequest(text="gray")
        )
        assert len(response["items"]) == 2
        assert all(item["evidence_kind"] == "semantic" for item in response["items"])
        assert all("exact_equivalence" not in item for item in response["items"])

    def test_unavailable_embeddings_leave_geometry_enabled(self, db_session, make_user):
        actor = make_user(superuser=True)
        configuration.update_settings(
            db_session, actor, {"enabled": True, "embeddings_enabled": True}
        )
        run = runs.start(db_session, actor)
        for _ in range(5):
            SimilarityProcessor(get_session_factory(), get_backend()).work_one()
        db_session.refresh(run)
        assert run.state == "completed"
        assert (
            json.loads(run.checkpoint_json)["embedding_failure_code"]
            == "embedding_not_configured"
        )


@pytest.fixture
def embedding_unit(
    db_session,
    local_storage,
    make_user,
    make_model,
    make_file,
    make_geometry_fingerprint,
    tmp_path,
    monkeypatch,
):
    from app.modules.inference import store
    from app.modules.inference.local import configured_provider
    from tests.paths import TESTDATA_DIR

    directory = local_embedding_assets(tmp_path / "assets")
    monkeypatch.setitem(_overlay, "embedding_local_model_dir", str(directory))
    monkeypatch.setitem(_overlay, "embedding_model_key", "two-tower-contract")
    actor = make_user(superuser=True)
    configuration.update_settings(
        db_session, actor, {"enabled": True, "embeddings_enabled": True}
    )
    content = (TESTDATA_DIR / "Calibration Cube.stl").read_bytes()
    file = make_file(
        make_model(),
        file_type=FileType.STL,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )
    backend = get_backend()
    file.path = backend.blob_key(file.model.slug, file.version, file.original_filename)
    db_session.add(file)
    db_session.commit()
    backend.write_stream(io.BytesIO(content), file.path)
    fp = make_geometry_fingerprint(file, state="ready")
    provider = configured_provider(get_session_factory())
    generation = store.initialize(get_session_factory(), provider)
    run = runs.start(db_session, actor)
    run.phase = "embeddings"
    run.checkpoint_json = json.dumps(
        {"generation_id": generation, "space_hash": provider.space.config_hash}
    )
    db_session.add(run)
    db_session.commit()
    return actor, file, fp, provider, generation, run


class TestEmbeddingRecovery:
    @pytest.mark.parametrize("failure", ["changed_source", "missing_content"])
    def test_contains_materialization_failure(
        self, db_session, embedding_unit, failure
    ):
        from app.db.models import ThumbnailRenderSlot
        from app.modules.storage import artifact_content

        _actor, file, fp, _provider, _generation, run = embedding_unit
        with artifact_content.resolve(
            file, backend=get_backend()
        ).materialize() as path:
            if failure == "changed_source":
                path.write_bytes(b"new content")
            else:
                path.unlink()
        assert SimilarityProcessor(get_session_factory(), get_backend()).work_one()
        db_session.refresh(run)
        assert run.state == "running"
        assert json.loads(run.counters_json)["embedding_failed"] == 1
        assert json.loads(run.checkpoint_json)["embedding_fingerprint_id"] == fp.id
        assert db_session.exec(select(PassageVector)).all() == []
        assert all(
            row.lease_token is None
            for row in db_session.exec(select(ThumbnailRenderSlot))
        )

    def test_skips_stale_source_without_embedding(self, db_session, embedding_unit):
        _actor, file, fp, _provider, _generation, run = embedding_unit
        file.sha256 = "f" * 64
        db_session.add(file)
        db_session.commit()
        SimilarityProcessor(get_session_factory(), get_backend()).work_one()
        db_session.refresh(run)
        assert json.loads(run.counters_json)["embedding_stale"] == 1
        assert json.loads(run.checkpoint_json)["embedding_fingerprint_id"] == fp.id
        assert db_session.exec(select(PassageVector)).all() == []

    def test_reuses_committed_vector(
        self, db_session, embedding_unit, make_passage_vector
    ):
        from app.db.models import IndexGeneration

        _actor, file, fp, _provider, generation, run = embedding_unit
        existing = make_passage_vector(
            db_session.get(IndexGeneration, generation), file
        )
        SimilarityProcessor(get_session_factory(), get_backend()).work_one()
        db_session.refresh(run)
        assert json.loads(run.counters_json)["embedding_cached"] == 1
        assert json.loads(run.checkpoint_json)["embedding_fingerprint_id"] == fp.id
        assert [row.id for row in db_session.exec(select(PassageVector))] == [
            existing.id
        ]

    def test_defers_when_geometry_slots_are_occupied(self, db_session, embedding_unit):
        from app.core.config import settings
        from app.modules.media import compute_slots

        *_rest, run = embedding_unit
        original = run.checkpoint_json
        for index in range(settings.max_render_jobs):
            assert compute_slots.acquire(db_session, f"preview-{index}") is not None
        SimilarityProcessor(get_session_factory(), get_backend()).work_one()
        db_session.refresh(run)
        assert run.state == "running"
        assert run.phase == "embeddings"
        assert json.loads(run.checkpoint_json) == json.loads(original)
        assert db_session.exec(select(PassageVector)).all() == []

    def test_preserves_geometry_when_embedding_configuration_changes(
        self, db_session, embedding_unit
    ):
        *_rest, run = embedding_unit
        checkpoint = json.loads(run.checkpoint_json)
        checkpoint["space_hash"] = "previous-configuration"
        run.checkpoint_json = json.dumps(checkpoint)
        db_session.add(run)
        db_session.commit()
        SimilarityProcessor(get_session_factory(), get_backend()).work_one()
        db_session.refresh(run)
        assert run.phase == "candidates"
        assert (
            json.loads(run.checkpoint_json)["embedding_failure_code"]
            == "embedding_configuration_changed"
        )
        assert db_session.exec(select(PassageVector)).all() == []

    def test_retries_native_capacity_without_skipping_unit(
        self, db_session, embedding_unit, monkeypatch
    ):
        from printstash_core.inference import EmbeddingError

        from app.modules.inference.local import LocalEmbeddingProvider

        *_rest, run = embedding_unit
        original = run.checkpoint_json

        def busy(*args, **kwargs):
            raise EmbeddingError("embedding_compute_busy")

        monkeypatch.setattr(LocalEmbeddingProvider, "embed", busy)
        SimilarityProcessor(get_session_factory(), get_backend()).work_one()
        db_session.refresh(run)
        assert run.state == "running"
        assert run.phase == "embeddings"
        assert json.loads(run.checkpoint_json) == json.loads(original)
        assert db_session.exec(select(PassageVector)).all() == []


class TestLearnedRetrievalFallback:
    @pytest.mark.parametrize("missing", ["configuration", "generation", "query_vector"])
    def test_preserves_geometric_proposals_without_learned_inputs(
        self, db_session, embedding_unit, missing, monkeypatch
    ):
        from app.db.models import IndexGeneration
        from app.modules.similarity.learned_retrieval import extend
        from app.modules.similarity.retrieval import Shortlist

        actor, _file, fp, _provider, generation, _run = embedding_unit
        if missing == "configuration":
            monkeypatch.setitem(_overlay, "embedding_local_model_dir", "")
        elif missing == "generation":
            row = db_session.get(IndexGeneration, generation)
            row.state = "retired"
            row.active_profile_key = None
            db_session.add(row)
            db_session.commit()
        proposals = Shortlist((101, 102), 2, 0, (101,))
        assert (
            extend(db_session, get_session_factory(), actor, fp, proposals, 10)
            == proposals
        )

    @pytest.mark.parametrize(
        "target", ["valid", "missing_fingerprint", "invalid_unit_key"]
    )
    def test_validates_learned_unit_lineage_before_interleaving(
        self,
        db_session,
        embedding_unit,
        make_file,
        make_model,
        make_passage_vector,
        make_geometry_fingerprint,
        target,
    ):
        from app.db.models import IndexGeneration
        from app.modules.similarity.learned_retrieval import extend
        from app.modules.similarity.retrieval import Shortlist

        actor, file, fp, _provider, generation_id, _run = embedding_unit
        generation = db_session.get(IndexGeneration, generation_id)
        make_passage_vector(generation, file)
        other = make_file(make_model())
        vector = make_passage_vector(generation, other)
        other_fp = (
            make_geometry_fingerprint(other, state="ready")
            if target != "missing_fingerprint"
            else None
        )
        if target == "invalid_unit_key":
            vector.unit_key = "legacy-unit"
            db_session.add(vector)
            db_session.commit()
        geometric = Shortlist((999,), 1, 0, (999,))
        result = extend(db_session, get_session_factory(), actor, fp, geometric, 10)
        assert result.fingerprint_ids == (
            (999, other_fp.id) if target == "valid" else (999,)
        )
        assert result.hash_hits == (999,)
        assert result.examined == 2
