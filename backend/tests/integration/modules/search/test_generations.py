"""Building, verification and cutover preserve the current serving generation."""

from datetime import timedelta

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlmodel import select

from app.core.errors import OperationError
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    IndexGeneration,
    PassageVector,
    SearchReconciliationState,
)
from app.modules.search import configuration, generations
from app.modules.search.passages import sync_subject
from app.schemas.inference import SearchSettings
from app.schemas.search_generations import GenerationProposal


@pytest.fixture
def retired_vector_batch(
    db_session,
    make_embedding_space,
    make_index_generation,
    make_passage_vector,
    make_document,
    make_search_passage,
):
    generation = make_index_generation(
        make_embedding_space(),
        active=False,
        version_token="e" * 32,
        retain_until=utcnow() - timedelta(seconds=1),
    )
    for number in range(130):
        document = make_document(f"Retired assembly guide {number}")
        passage = make_search_passage(SearchSubject(SubjectType.DOCUMENT, document.id))
        make_passage_vector(generation, passage=passage)
    return generation


@pytest.fixture
def local_caption_upgrade(db_session, warm_model, make_user, make_model):
    from app.modules.search import captions
    from app.schemas.captions import CaptionPatch

    model, generation = warm_model
    actor = make_user(superuser=True)
    subject = SearchSubject(SubjectType.MODEL, make_model("Captioned bracket").id)
    configuration.update(
        db_session,
        SearchSettings(enabled=True, local_models_enabled=True),
        actor_id=actor.id,
    )
    captions.patch(
        db_session, actor, subject, CaptionPatch(action="edit", text="Mounting bracket")
    )
    db_session.commit()
    return actor, model, generation


class TestEnsureCaptionRecipe:
    def test_upgrades_an_active_local_text_generation_for_captions(
        self, db_session, local_caption_upgrade
    ):
        from app.modules.search.text_inputs import TextRecipe

        _, _, previous = local_caption_upgrade

        assert generations.ensure_caption_recipe(db_session) is True
        assert generations.ensure_caption_recipe(db_session) is False

        rows = db_session.exec(
            select(IndexGeneration).order_by(IndexGeneration.id)
        ).all()
        assert [(row.id, row.state) for row in rows[:1]] == [(previous.id, "active")]
        assert len(rows) == 2
        assert rows[1].state == "building"
        assert (
            TextRecipe.for_space(
                generations.contract(db_session, rows[1])
            ).passage_version
            == 2
        )

    @pytest.mark.parametrize(
        "missing", ["local-consent", "model", "actor", "active-generation"]
    )
    def test_defers_a_local_caption_upgrade_without_its_prerequisites(
        self, db_session, local_caption_upgrade, missing
    ):
        actor, model, previous = local_caption_upgrade
        if missing == "local-consent":
            configuration.update(
                db_session,
                SearchSettings(enabled=True, local_models_enabled=False),
                actor_id=actor.id,
            )
        elif missing == "model":
            (model.directory / "manifest.json").unlink()
        elif missing == "actor":
            actor.is_active = False
            db_session.add(actor)
        else:
            previous.state = "retired"
            db_session.add(previous)
        db_session.commit()

        assert generations.ensure_caption_recipe(db_session) is False

        assert db_session.exec(select(IndexGeneration.id)).all() == [previous.id]


class TestPrepare:
    @pytest.mark.parametrize("profile", ["thumbnail", "multiview", "point_cloud"])
    def test_rejects_incompatible_generation_modalities(
        self, db_session, warm_model, profile
    ):
        model, _ = warm_model

        with pytest.raises(OperationError, match="embedding_alignment_unavailable"):
            generations.proposal_space(
                db_session, GenerationProposal(local_model_id=model.id, profile=profile)
            )

        assert len(db_session.exec(select(IndexGeneration)).all()) == 1

    def test_admits_a_small_logical_index_budget(self, db_session, generation_setup):
        actor, endpoint = generation_setup
        configuration.update(
            db_session, SearchSettings(enabled=True, max_index_bytes=2 * 1024**2)
        )
        db_session.commit()

        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )

        assert proposal.state == "building"
        assert proposal.estimated_bytes < 2 * 1024**2

    def test_preserves_native_floats_when_switching_to_reviewed_mrl(
        self,
        db_session,
        generation_setup,
        healthy_embeddings,
        advance_generation,
        make_inference_endpoint,
        monkeypatch,
    ):
        from printstash_core.inference.model_capabilities import MXBAI_LARGE_V1
        from sqlalchemy import text

        from app.core.config import _overlay
        from app.modules.inference.endpoint import EndpointConfig
        from app.modules.search import vector_index

        actor, _ = generation_setup
        monkeypatch.setitem(_overlay, "search_native_vectors_enabled", True)
        endpoint = make_inference_endpoint(
            native_dimension=1024,
            config=EndpointConfig(
                base_url="http://inference.test/v1",
                model="test-mxbai",
                model_repo=MXBAI_LARGE_V1.repository,
                revision=MXBAI_LARGE_V1.revision,
            ),
        )
        healthy_embeddings.dimension = 1024
        first = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        advance_generation(first.id)
        healthy_embeddings.requests.clear()

        replacement = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="sqlite_vec", index_dimension=128
            ),
        )
        advance_generation(replacement.id)
        db_session.expire_all()

        assert healthy_embeddings.requests == []
        assert (replacement.native_dimension, replacement.index_dimension) == (
            1024,
            128,
        )
        vector = db_session.exec(
            select(PassageVector).where(PassageVector.generation_id == replacement.id)
        ).one()
        assert len(vector.vector_blob) == 4096
        assert vector.native_dimension == 1024
        assert (
            db_session.execute(
                text(
                    f"SELECT vec_length(embedding) FROM {vector_index.table_name(replacement.id, 'sqlite')}"
                )
            ).scalar_one()
            == 128
        )

    def test_rejects_unreviewed_truncation(
        self, db_session, generation_setup, healthy_embeddings, advance_generation
    ):
        from printstash_core.inference import EmbeddingError

        actor, endpoint = generation_setup
        first = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        advance_generation(first.id)

        with pytest.raises(EmbeddingError, match="embedding_mrl_unavailable"):
            generations.prepare(
                db_session,
                actor,
                GenerationProposal(endpoint_id=endpoint.id, index_dimension=2),
            )

        assert db_session.get(IndexGeneration, first.id).state == "active"
        assert (
            db_session.exec(
                select(IndexGeneration.id).where(IndexGeneration.state == "building")
            ).all()
            == []
        )

    def test_rejects_capacity_overcommit(
        self, db_session, generation_setup, healthy_embeddings, advance_generation
    ):
        actor, endpoint = generation_setup
        first = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        advance_generation(first.id)
        configuration.update(
            db_session, SearchSettings(enabled=True, max_index_bytes=1024**2)
        )

        with pytest.raises(OperationError) as caught:
            generations.prepare(
                db_session,
                actor,
                GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
            )

        assert caught.value.kind.value == "capacity"
        assert db_session.exec(
            select(IndexGeneration.id).where(IndexGeneration.state == "active")
        ).all() == [first.id]
        assert (
            db_session.exec(
                select(IndexGeneration.id).where(IndexGeneration.state == "building")
            ).all()
            == []
        )

    def test_prepares_a_building_generation(self, db_session, generation_setup):
        actor, endpoint = generation_setup

        result = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", auto_activate=False
            ),
        )

        assert (
            result.state,
            result.phase,
            result.profile,
            result.native_dimension,
        ) == ("building", "reconcile", "semantic_text", 4)
        assert result.job_id
        assert (
            db_session.exec(
                select(IndexGeneration).where(IndexGeneration.state == "active")
            ).all()
            == []
        )

    def test_refuses_another_build_for_the_profile(self, db_session, generation_setup):
        actor, endpoint = generation_setup
        generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )

        with pytest.raises(OperationError, match="search_generation_building"):
            generations.prepare(
                db_session,
                actor,
                GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
            )

        assert len(db_session.exec(select(IndexGeneration)).all()) == 1

    def test_requires_ai_opt_in(self, db_session, generation_setup):
        actor, endpoint = generation_setup
        configuration.update(db_session, SearchSettings())

        with pytest.raises(OperationError, match="search_ai_disabled"):
            generations.prepare(
                db_session, actor, GenerationProposal(endpoint_id=endpoint.id)
            )


class TestActivate:
    def test_refuses_content_added_after_verification(
        self,
        db_session,
        generation_setup,
        healthy_embeddings,
        advance_generation,
        make_document,
    ):
        actor, endpoint = generation_setup
        first = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        advance_generation(first.id)
        second = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", auto_activate=False
            ),
        )
        advance_generation(second.id)
        added = make_document("New instruction")
        sync_subject(db_session, SearchSubject(SubjectType.DOCUMENT, added.id))
        db_session.commit()

        with pytest.raises(OperationError, match="search_generation_incomplete"):
            generations.activate(db_session, second.id, second.version_token)

        assert db_session.exec(
            select(IndexGeneration.id).where(IndexGeneration.state == "active")
        ).all() == [first.id]

    def test_activates_a_verified_generation(
        self, db_session, generation_setup, healthy_embeddings, advance_generation
    ):
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", auto_activate=False
            ),
        )
        advance_generation(proposal.id)

        result = generations.activate(db_session, proposal.id, proposal.version_token)

        assert result.state == "active"
        assert result.indexed == result.eligible == 1
        assert db_session.exec(select(PassageVector.input_hash)).one()

    def test_replaces_the_old_active_atomically(
        self, db_session, generation_setup, healthy_embeddings, advance_generation
    ):
        actor, endpoint = generation_setup
        first = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", auto_activate=False
            ),
        )
        advance_generation(first.id)
        generations.activate(db_session, first.id, first.version_token)
        second = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id,
                index_backend="numpy",
                document_prefix="passage: ",
                auto_activate=False,
            ),
        )
        advance_generation(second.id)

        generations.activate(db_session, second.id, second.version_token)
        db_session.expire_all()

        assert db_session.exec(
            select(IndexGeneration.id).where(IndexGeneration.state == "active")
        ).all() == [second.id]
        assert db_session.get(IndexGeneration, first.id).state == "retired"
        assert (
            ensure_utc(db_session.get(IndexGeneration, first.id).retain_until)
            > utcnow()
        )

    def test_refuses_unverified_generations(self, db_session, generation_setup):
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, auto_activate=False),
        )

        with pytest.raises(OperationError, match="search_generation_not_ready"):
            generations.activate(db_session, proposal.id, proposal.version_token)

    def test_rejects_stale_proposal_versions(self, db_session, generation_setup):
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session, actor, GenerationProposal(endpoint_id=endpoint.id)
        )

        with pytest.raises(OperationError, match="search_proposal_changed"):
            generations.activate(db_session, proposal.id, "f" * 32)


class TestCancel:
    def test_cancels_durable_work(
        self, db_session, generation_setup, healthy_embeddings, advance_generation
    ):
        actor, endpoint = generation_setup
        old = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        advance_generation(old.id)
        proposal = generations.prepare(
            db_session, actor, GenerationProposal(endpoint_id=endpoint.id)
        )

        result = generations.cancel(db_session, proposal.id, proposal.version_token)

        assert (result.state, result.phase) == ("cancelled", "cancelled")
        assert db_session.get(IndexGeneration, old.id).state == "active"
        assert db_session.get(IndexGeneration, proposal.id).building_profile_key is None


class TestPruneOne:
    def test_prunes_vectors_in_bounded_batches(self, db_session, retired_vector_batch):
        generation = retired_vector_batch

        pruned = generations.prune_one(db_session)

        assert pruned is True
        assert (
            len(
                db_session.exec(
                    select(PassageVector.id).where(
                        PassageVector.generation_id == generation.id
                    )
                ).all()
            )
            == 2
        )
        assert db_session.get(IndexGeneration, generation.id).phase == "pruning"

    def test_preserves_rollback_retention_after_readers_finish(
        self, db_session, generation_setup, healthy_embeddings, advance_generation
    ):
        actor, endpoint = generation_setup
        first = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        advance_generation(first.id)
        second = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        advance_generation(second.id)

        assert generations.prune_one(db_session) is False
        assert db_session.exec(select(PassageVector.generation_id)).all() == [
            first.id,
            second.id,
        ]

    def test_prunes_expired_generations_with_their_checkpoints(
        self, db_session, generation_setup, healthy_embeddings, advance_generation
    ):
        actor, endpoint = generation_setup
        first = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        advance_generation(first.id)
        second = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        advance_generation(second.id)
        old = db_session.get(IndexGeneration, first.id)
        old.retain_until = utcnow() - timedelta(seconds=1)
        db_session.add(old)
        db_session.commit()

        assert generations.prune_one(db_session) is True
        assert generations.prune_one(db_session) is True

        assert db_session.exec(select(PassageVector.generation_id)).all() == [second.id]
        assert db_session.get(IndexGeneration, first.id).phase == "pruned"
        assert (
            db_session.exec(
                select(SearchReconciliationState.subject_type).where(
                    SearchReconciliationState.subject_type.like(f"g{first.id}:%")
                )
            ).all()
            == []
        )

    def test_retains_old_vectors_while_a_reader_is_pinned(
        self, db_session, generation_setup, healthy_embeddings, advance_generation
    ):
        actor, endpoint = generation_setup
        first = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", auto_activate=False
            ),
        )
        advance_generation(first.id)
        generations.activate(db_session, first.id, first.version_token)
        pin = generations.pin(db_session, first.id)
        second = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", auto_activate=False
            ),
        )
        advance_generation(second.id)
        generations.activate(db_session, second.id, second.version_token)
        old = db_session.get(IndexGeneration, first.id)
        old.retain_until = utcnow() - timedelta(seconds=1)
        db_session.add(old)
        db_session.commit()

        pruned = generations.prune_one(db_session)

        assert pruned is False
        assert db_session.exec(
            select(PassageVector.id).where(PassageVector.generation_id == first.id)
        ).all()
        generations.unpin(db_session, pin)


class TestEstimate:
    def test_estimates_generation_without_starting_work(
        self, db_session, generation_setup, healthy_embeddings, make_document
    ):
        actor, endpoint = generation_setup
        document = make_document("A newly indexed guide")
        sync_subject(db_session, SearchSubject(SubjectType.DOCUMENT, document.id))
        before = db_session.exec(select(IndexGeneration.id)).all()
        result = generations.estimate(
            db_session, GenerationProposal(endpoint_id=endpoint.id)
        )
        assert result.passages >= 1
        assert result.estimated_bytes > 1024**2
        assert result.existing_bytes == 0
        assert result.fits_budget is True
        assert result.estimated_seconds is None
        assert db_session.exec(select(IndexGeneration.id)).all() == before
        assert healthy_embeddings.requests == []

    def test_reports_measured_generation_eta(self, db_session, generation_setup):
        actor, endpoint = generation_setup
        result = generations.prepare(
            db_session, actor, GenerationProposal(endpoint_id=endpoint.id)
        )
        row = db_session.get(IndexGeneration, result.id)
        row.created_at = utcnow() - timedelta(seconds=60)
        row.last_activity_at = row.created_at + timedelta(seconds=60)
        row.processed = 10
        row.phase = "backfill"
        db_session.add(row)
        db_session.flush()
        result = generations.read(db_session, row)
        assert result.created_at == ensure_utc(row.created_at)
        assert result.last_activity_at == ensure_utc(row.last_activity_at)
        assert result.eta_seconds == result.eligible * 6
