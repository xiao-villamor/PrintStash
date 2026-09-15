"""Point indexing shares visual source fencing and durable generation control."""

import hashlib

import pytest
from printstash_core.inference import EmbeddingError
from printstash_core.inference.context import InferenceContext
from printstash_core.search.point_inputs import PointRecipe
from sqlmodel import select

from app.core.config import _overlay
from app.core.errors import OperationError
from app.core.time import utcnow
from app.db.models import FileType, IndexGeneration, PassageVector
from app.modules.inference import model_cache
from app.modules.media import visual_render
from app.modules.search import (
    configuration,
    generations,
    indexing,
    visual_index,
    visual_query,
)
from app.modules.search.retrieval import search
from app.schemas.inference import SearchSettings
from app.schemas.search_generations import GenerationProposal
from tests.factories.embeddings import local_embedding_assets, point_embedding_assets
from tests.factories.geometry import tetrahedron


@pytest.fixture
def point_setup(
    db_session, tmp_path, monkeypatch, make_user, make_model, make_file, make_collection
):
    root = tmp_path / "cache"
    clip = model_cache.inspect(local_embedding_assets(root / "clip"))
    point = model_cache.inspect(point_embedding_assets(root / "point"))
    monkeypatch.setitem(_overlay, "embedding_cache_dir", root)
    monkeypatch.setitem(_overlay, "embedding_local_model_dir", "")
    actor = make_user(superuser=True)
    model = make_model("Opaque object", collection=make_collection("Shared"))
    payload = tetrahedron().export(file_type="stl")
    path = tmp_path / "part.stl"
    path.write_bytes(payload)
    file = make_file(
        model,
        file_type=FileType.STL,
        path=str(path),
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        external=True,
    )
    configuration.update(
        db_session, SearchSettings(enabled=True, local_models_enabled=True)
    )
    db_session.commit()
    return actor, model, file, clip, point


@pytest.fixture
def point_ready_fallback(db_session, point_setup, advance_generation):
    actor, model, file, clip, point = point_setup
    fallback = generations.prepare(
        db_session,
        actor,
        GenerationProposal(
            local_model_id=clip.id, profile="thumbnail", index_backend="numpy"
        ),
    )
    advance_generation(fallback.id)
    return point_setup


def point_proposal(model):
    return GenerationProposal(
        local_model_id=model.id, profile="point_cloud", index_backend="numpy"
    )


class TestPointIndex:
    def test_requires_a_ready_visual_fallback(self, db_session, point_setup):
        actor, model, file, clip, point = point_setup
        with pytest.raises(OperationError, match="search_visual_fallback_required"):
            generations.prepare(db_session, actor, point_proposal(point))
        assert db_session.exec(select(IndexGeneration)).all() == []

    def test_publishes_one_current_point_unit(
        self, db_session, point_ready_fallback, advance_generation
    ):
        actor, model, file, clip, point = point_ready_fallback
        proposal = generations.prepare(db_session, actor, point_proposal(point))
        advance_generation(proposal.id)
        db_session.expire_all()
        generation = db_session.get(IndexGeneration, proposal.id)
        assert generation.state == "active"
        assert generations.counts(db_session, generation) == (1, 1, 0)
        vector = db_session.exec(
            select(PassageVector).where(PassageVector.generation_id == proposal.id)
        ).one()
        assert (
            vector.unit_kind,
            vector.unit_key,
            vector.input_hash,
            vector.file_id,
            vector.subject_id,
        ) == ("point_cloud", f"file:{file.id}:point", file.sha256, file.id, model.id)
        result = search(db_session, actor, "gray", legs=("point_cloud",))
        assert [item.subject_id for item in result.items] == [model.id]
        assert [reason.leg for reason in result.items[0].evidence] == ["point_cloud"]

    @pytest.mark.parametrize("mutation", ["hash", "trash", "cancel"])
    def test_fences_late_point_publication(
        self, db_session, point_ready_fallback, mutation
    ):
        actor, model, file, clip, point = point_ready_fallback
        proposal = generations.prepare(db_session, actor, point_proposal(point))
        # Claim the new build explicitly; an active fallback may also have work.
        for _ in range(4):
            generation_id, token = indexing.claim(db_session)
            if generation_id == proposal.id:
                break
            indexing.release(db_session, generation_id, token)
        else:
            raise AssertionError("new point build was not claimed")
        generation = db_session.get(IndexGeneration, generation_id)
        source = visual_index.pending(db_session, generation)
        if mutation == "hash":
            file.sha256 = "e" * 64
            db_session.add(file)
        elif mutation == "trash":
            model.deleted_at = utcnow()
            db_session.add(model)
        else:
            generations.cancel(db_session, generation_id, proposal.version_token)
        db_session.commit()
        assert not visual_index.publish(
            db_session, generation_id, token, source, ((1.0, 0, 0),), None
        )
        assert (
            db_session.exec(
                select(PassageVector).where(
                    PassageVector.generation_id == generation_id
                )
            ).all()
            == []
        )

    def test_preserves_thumbnail_results_when_point_encoding_fails(
        self, db_session, point_ready_fallback, advance_generation, monkeypatch
    ):
        actor, model, file, clip, point = point_ready_fallback
        proposal = generations.prepare(db_session, actor, point_proposal(point))
        advance_generation(proposal.id)
        original = visual_query.embedding_provider

        def unavailable(session, space):
            if space.profile == "point_cloud":
                raise EmbeddingError("embedding_runtime_unavailable")
            return original(session, space)

        monkeypatch.setattr(visual_query, "embedding_provider", unavailable)
        result = search(db_session, actor, "gray")
        assert result.items[0].subject_id == model.id
        assert [reason.leg for reason in result.items[0].evidence] == ["thumbnail"]
        assert result.leg_errors == {"point_cloud": "embedding_runtime_unavailable"}

    @pytest.mark.parametrize("kind", ["stl", "3mf", "step"])
    def test_samples_points_in_the_isolated_geometry_worker(self, point_setup, kind):
        from pathlib import Path

        actor, model, file, clip, point = point_setup
        source = Path(file.path)
        if kind == "3mf":
            source = source.with_suffix(".3mf")
            source.write_bytes(tetrahedron().export(file_type="3mf"))
        elif kind == "step":
            from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
            from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

            source = source.with_suffix(".step")
            writer = STEPControl_Writer()
            writer.Transfer(BRepPrimAPI_MakeBox(10, 20, 30).Shape(), STEPControl_AsIs)
            writer.Write(str(source))
        before = source.read_bytes()
        result = visual_render.render(
            source,
            file_type=kind,
            recipe=PointRecipe.for_space(point.manifest.space()),
            context=InferenceContext.bounded(20),
        )
        assert result.thumbnail is None
        assert len(result.views) == 1
        assert len(result.views[0].points) == 240000
        assert source.read_bytes() == before

    def test_hides_private_point_matches(
        self,
        db_session,
        point_ready_fallback,
        advance_generation,
        make_user,
        make_collection,
        make_model,
        make_file,
    ):
        actor, model, file, clip, point = point_ready_fallback
        hidden = make_model("Private object", collection=make_collection("Private"))
        make_file(
            hidden,
            file_type=FileType.STL,
            path=file.path,
            sha256=file.sha256,
            size_bytes=file.size_bytes,
            external=True,
        )
        viewer = make_user()
        from app.db.models import Collection, CollectionRole
        from tests.factories import grant_collection_role

        grant_collection_role(
            db_session,
            viewer,
            db_session.get(Collection, model.collection_id),
            CollectionRole.VIEW,
        )
        proposal = generations.prepare(db_session, actor, point_proposal(point))
        advance_generation(proposal.id)
        result = search(db_session, viewer, "gray", legs=("point_cloud",))
        assert [item.subject_id for item in result.items] == [model.id]

    def test_quarantines_invalid_point_geometry(self, db_session, point_ready_fallback):
        from datetime import timedelta
        from pathlib import Path

        from app.db.models import SearchIndexFailure
        from app.db.session import get_session_factory

        actor, model, file, clip, point = point_ready_fallback
        Path(file.path).write_bytes(b"invalid mesh")
        file.sha256 = hashlib.sha256(b"invalid mesh").hexdigest()
        db_session.add(file)
        db_session.commit()
        proposal = generations.prepare(db_session, actor, point_proposal(point))
        processor = indexing.IndexProcessor(get_session_factory())
        failure = None
        for _ in range(20):
            processor.work_one()
            db_session.expire_all()
            failure = db_session.exec(
                select(SearchIndexFailure).where(
                    SearchIndexFailure.generation_id == proposal.id
                )
            ).first()
            if failure is not None:
                if failure.state == "quarantined":
                    break
                failure.retry_after = utcnow() - timedelta(seconds=1)
                db_session.add(failure)
                db_session.commit()
        assert failure is not None
        assert (
            failure.state,
            failure.attempts,
            failure.file_id,
            failure.input_hash,
        ) == ("quarantined", 3, file.id, file.sha256)
        assert (
            db_session.exec(
                select(PassageVector).where(PassageVector.generation_id == proposal.id)
            ).all()
            == []
        )
