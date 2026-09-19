"""Human actions fence late VLM results and synchronously invalidate search."""

import json
from datetime import timedelta

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlmodel import select

from app.core.errors import OperationError
from app.core.time import utcnow
from app.db.models import (
    FileType,
    Model,
    PassageVector,
    SearchLexicalState,
    SearchPassage,
    SubjectCaption,
)
from app.db.session import get_session_factory
from app.modules.search import captions, configuration
from app.modules.search.caption_worker import CaptionProcessor, sweep
from app.modules.search.lexical_index import rebuild_partition
from app.modules.search.lexical_query import ordered_passages
from app.modules.search.passages import sync_subject
from app.schemas.captions import CaptionPatch
from app.schemas.inference import SearchSettings
from tests.fakes.captions import CaptionProvider, rendered_preview


@pytest.fixture
def caption_setup(
    db_session, make_user, make_model, make_file, make_inference_endpoint
):
    actor = make_user(superuser=True)
    model = make_model("Anonymous part", description="Human description")
    file = make_file(model, file_type=FileType.STL)
    endpoint = make_inference_endpoint(kind="chat", supports_images=True)
    configuration.update(
        db_session,
        SearchSettings(
            enabled=True,
            captions_enabled=True,
            send_rendered_images=True,
            chat_endpoint_id=endpoint.id,
        ),
        actor_id=actor.id,
    )
    return actor, SearchSubject(SubjectType.MODEL, model.id), file, endpoint


class TestCaptions:
    def test_fails_queued_captions_whose_source_was_trashed(
        self, db_session, caption_setup
    ):
        actor, subject, file, _ = caption_setup
        captions.patch(db_session, actor, subject, CaptionPatch(action="generate"))
        file.deleted_at = utcnow()
        db_session.add(file)
        db_session.commit()
        provider = CaptionProvider()

        assert (
            CaptionProcessor(
                get_session_factory(),
                provider_factory=lambda *_: provider,
                image_renderer=rendered_preview,
            ).work_one()
            is True
        )

        db_session.expire_all()
        row = captions.lookup(db_session, subject)
        assert (row.phase, row.error_code, row.text) == (
            "failed",
            "caption_source_changed",
            "",
        )
        assert provider.requests == []

    def test_records_unexpected_caption_renderer_failures_safely(
        self, db_session, caption_setup
    ):
        actor, subject, _, _ = caption_setup
        provider = CaptionProvider()

        def broken_renderer(*_args):
            raise RuntimeError("private-render-path")

        result = CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=broken_renderer,
        ).work_one()

        assert result is True
        db_session.expire_all()
        caption = captions.read(db_session, actor, subject)
        assert (caption.phase, caption.error_code, caption.text) == (
            "pending",
            "caption_generation_failed",
            "",
        )
        assert provider.requests == []

    def test_finishes_exhausted_durable_caption_jobs(self, db_session, caption_setup):
        from app.db.models import BackgroundJob
        from app.runtime.jobs import registry

        actor, subject, _, _ = caption_setup
        captions.patch(db_session, actor, subject, CaptionPatch(action="generate"))
        row = captions.lookup(db_session, subject)
        job_id = registry.create(actor.id, kind="ai_caption", session=db_session)
        row.job_id = job_id
        row.phase, row.attempts, row.lease_token = "running", 3, "a" * 32
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db_session.add(row)
        db_session.commit()

        assert not CaptionProcessor(get_session_factory()).work_one()

        db_session.expire_all()
        assert (
            captions.read(db_session, actor, subject).error_code
            == "caption_attempts_exhausted"
        )
        job = db_session.get(BackgroundJob, job_id)
        assert (job.state, json.loads(job.status_json)["error"]) == (
            "failed",
            "caption_attempts_exhausted",
        )

    def test_fences_caption_egress_after_actor_loss(self, db_session, caption_setup):
        from app.db.models import User

        actor, subject, _, _ = caption_setup
        revocations = []

        def revoke(*_args):
            with get_session_factory().scoped_session() as session:
                current = session.get(User, actor.id)
                current.is_active = False
                session.add(current)
                session.commit()
                revocations.append(current.is_active)
            return rendered_preview()

        provider = CaptionProvider()
        assert CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=revoke,
        ).work_one()
        assert revocations == [False]
        assert provider.requests == []
        db_session.expire_all()
        assert captions.lookup(db_session, subject).text == ""

    def test_suppresses_caption_egress_with_the_master_off(
        self, db_session, caption_setup
    ):
        configuration.update(
            db_session,
            configuration.settings(db_session).model_copy(update={"enabled": False}),
        )
        db_session.commit()
        provider = CaptionProvider()
        processor = CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=rendered_preview,
        )
        assert not processor.work_one()
        assert provider.requests == []
        assert db_session.exec(select(SubjectCaption)).all() == []

    def test_keeps_generated_text_separate_from_human_description(
        self, db_session, caption_setup
    ):
        actor, subject, _, _ = caption_setup
        provider = CaptionProvider()
        processor = CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=rendered_preview,
        )
        assert processor.work_one()
        db_session.expire_all()
        result = captions.read(db_session, actor, subject)
        assert result.state == "generated"
        assert result.phase == "ready", result.error_code
        assert result.text == "A flanged mounting bracket"
        assert (
            db_session.get(Model, subject.subject_id).description == "Human description"
        )
        assert not configuration.settings(db_session).nl_filters_enabled
        assert provider.requests[0].image_jpegs[0].startswith(b"\xff\xd8\xff")
        assert provider.requests[0].text == "Describe this object."
        assert len(provider.requests) == 1
        assert not processor.work_one()

    @pytest.mark.parametrize("action", ["edit", "dismiss"])
    def test_fences_a_late_completion(self, db_session, caption_setup, action):
        actor, subject, _, _ = caption_setup

        def change():
            with get_session_factory().scoped_session() as session:
                captions.patch(
                    session,
                    actor,
                    subject,
                    CaptionPatch(
                        action=action,
                        text="Human caption" if action == "edit" else None,
                    ),
                )

        provider = CaptionProvider(before_reply=change)
        assert CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=rendered_preview,
        ).work_one()
        db_session.expire_all()
        result = captions.read(db_session, actor, subject)
        assert result.state == ("edited" if action == "edit" else "dismissed")
        assert result.text == ("Human caption" if action == "edit" else "")
        assert sweep(db_session) == 0

    @pytest.mark.parametrize("action", ["edit", "dismiss"])
    def test_preserves_human_decisions_when_the_source_changes(
        self, db_session, caption_setup, action
    ):
        actor, subject, file, _ = caption_setup
        captions.patch(
            db_session,
            actor,
            subject,
            CaptionPatch(
                action=action, text="Human caption" if action == "edit" else None
            ),
        )
        file.sha256 = "f" * 64
        db_session.add(file)
        db_session.commit()
        assert sweep(db_session) == 0
        assert captions.read(db_session, actor, subject).state == (
            "edited" if action == "edit" else "dismissed"
        )
        with pytest.raises(OperationError, match="caption_reset_required"):
            captions.patch(db_session, actor, subject, CaptionPatch(action="generate"))
        reset = captions.patch(db_session, actor, subject, CaptionPatch(action="reset"))
        assert (reset.state, reset.phase, reset.text) == ("generated", "pending", "")

    @pytest.mark.parametrize(
        "value",
        [
            {"caption": ""},
            {"caption": "x" * 2049},
            {"caption": 3},
            {"caption": "text", "extra": True},
        ],
    )
    def test_bounds_invalid_output_retries(self, db_session, caption_setup, value):
        actor, subject, _, _ = caption_setup
        provider = CaptionProvider(value)
        processor = CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=rendered_preview,
        )
        for _ in range(3):
            assert processor.work_one()
            db_session.expire_all()
            row = captions.lookup(db_session, subject)
            row.retry_after = utcnow() - timedelta(seconds=1)
            db_session.add(row)
            db_session.commit()
        assert not processor.work_one()
        result = captions.read(db_session, actor, subject)
        assert result.phase == "failed"
        assert result.text == ""
        assert result.error_code == "caption_output_invalid"
        assert len(provider.requests) == 3

    @pytest.mark.parametrize("switch", ["captions_enabled", "enabled"])
    def test_rechecks_consent_after_rendering(self, db_session, caption_setup, switch):
        actor, subject, _, _ = caption_setup
        provider = CaptionProvider()

        def revoke(*args):
            with get_session_factory().scoped_session() as session:
                configuration.update(
                    session,
                    configuration.settings(session).model_copy(update={switch: False}),
                )
            return rendered_preview()

        assert CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=revoke,
        ).work_one()
        assert provider.requests == []
        db_session.expire_all()
        assert captions.read(db_session, actor, subject).text == ""

    def test_defaults_off_with_a_configured_endpoint(self, db_session, caption_setup):
        _, _, _, endpoint = caption_setup
        configuration.update(db_session, SearchSettings(chat_endpoint_id=endpoint.id))
        provider = CaptionProvider()
        assert not CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=rendered_preview,
        ).work_one()
        assert provider.requests == []
        assert db_session.exec(select(SubjectCaption)).all() == []

    def test_reclaims_an_expired_lease_after_restart(self, db_session, caption_setup):
        actor, subject, _, _ = caption_setup
        captions.patch(db_session, actor, subject, CaptionPatch(action="generate"))
        row = captions.lookup(db_session, subject)
        row.phase, row.lease_token = "running", "a" * 32
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db_session.add(row)
        db_session.commit()
        provider = CaptionProvider()
        assert CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=rendered_preview,
        ).work_one()
        db_session.expire_all()
        assert captions.read(db_session, actor, subject).phase == "ready"

    def test_rejects_a_stale_edit_version(self, db_session, caption_setup):
        actor, subject, _, _ = caption_setup
        first = captions.patch(
            db_session, actor, subject, CaptionPatch(action="edit", text="First")
        )
        captions.patch(
            db_session,
            actor,
            subject,
            CaptionPatch(action="dismiss", version_token=first.version_token),
        )
        with pytest.raises(OperationError, match="caption_changed"):
            captions.patch(
                db_session,
                actor,
                subject,
                CaptionPatch(
                    action="edit", text="Stale", version_token=first.version_token
                ),
            )

    def test_keeps_one_lexical_recipe_during_semantic_coexistence(
        self,
        db_session,
        caption_setup,
        make_embedding_space,
        make_index_generation,
        make_passage_vector,
    ):
        actor, subject, file, _ = caption_setup
        captions.patch(
            db_session,
            actor,
            subject,
            CaptionPatch(action="edit", text="Zygomatic fixture"),
        )
        passages = db_session.exec(
            select(SearchPassage)
            .where(
                SearchPassage.subject_id == subject.subject_id,
                SearchPassage.subject_type == "model",
            )
            .order_by(SearchPassage.recipe_version)
        ).all()
        assert [p.recipe_version for p in passages] == [1, 2]
        assert "Zygomatic" not in passages[0].text
        before = passages[0].content_hash
        generation = make_index_generation(make_embedding_space())
        vector = make_passage_vector(
            generation,
            file,
            passage_id=passages[1].id,
            unit_kind="passage",
            unit_key=f"passage:{passages[1].id}",
            input_hash=passages[1].content_hash,
        )
        vector_id = vector.id
        rebuild_partition(db_session)
        db_session.commit()
        allowed = select(SearchPassage.id)
        for force_like in (False, True):
            assert (
                len(
                    db_session.exec(
                        ordered_passages(
                            db_session, "Zygomatic", allowed, force_like=force_like
                        )
                    ).all()
                )
                == 1
            )
            assert (
                len(
                    db_session.exec(
                        ordered_passages(
                            db_session, "Anonymous", allowed, force_like=force_like
                        )
                    ).all()
                )
                == 1
            )
        captions.patch(db_session, actor, subject, CaptionPatch(action="dismiss"))
        assert db_session.get(PassageVector, vector_id, populate_existing=True) is None
        assert (
            db_session.exec(ordered_passages(db_session, "Zygomatic", allowed)).all()
            == []
        )
        assert db_session.get(SearchPassage, passages[0].id).content_hash == before
        for _ in range(3):
            sync_subject(db_session, subject)
        assert db_session.get(SearchLexicalState, 1).document_count == 1

    @pytest.mark.parametrize(
        "change", ["source_hash", "trash_file", "trash_model", "revoke_actor"]
    )
    def test_rejects_stale_source_completion(self, db_session, caption_setup, change):
        actor, subject, file, _ = caption_setup

        def changed():
            with get_session_factory().scoped_session() as session:
                from app.db.models import File, User

                if change == "source_hash":
                    row = session.get(File, file.id)
                    row.sha256 = "9" * 64
                elif change == "trash_file":
                    row = session.get(File, file.id)
                    row.deleted_at = utcnow()
                elif change == "trash_model":
                    row = session.get(Model, subject.subject_id)
                    row.deleted_at = utcnow()
                else:
                    row = session.get(User, actor.id)
                    row.is_active = False
                session.add(row)
                session.commit()

        provider = CaptionProvider(before_reply=changed)
        assert CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=rendered_preview,
        ).work_one()
        db_session.expire_all()
        assert captions.lookup(db_session, subject).text == ""
        assert (
            db_session.exec(
                select(SearchPassage).where(SearchPassage.text.contains("flanged"))
            ).all()
            == []
        )

    def test_renders_a_real_artifact_without_an_embedding_model(
        self, db_session, caption_setup, tmp_path
    ):
        import hashlib
        from io import BytesIO

        from PIL import Image
        from printstash_core.inference import InferenceContext

        from app.modules.search.caption_worker import render_image
        from tests.factories.geometry import tetrahedron

        _, _, file, _ = caption_setup
        data = tetrahedron().export(file_type="stl")
        path = tmp_path / "caption.stl"
        path.write_bytes(data)
        file.path, file.sha256, file.size_bytes, file.is_external = (
            str(path),
            hashlib.sha256(data).hexdigest(),
            len(data),
            True,
        )
        db_session.add(file)
        db_session.commit()
        data = render_image(get_session_factory(), file, InferenceContext.bounded(30))
        image = Image.open(BytesIO(data))
        assert image.format == "JPEG"
        assert image.size == (384, 384)
        assert len(data) < 512 * 1024

    def test_upgrades_active_text_to_caption_recipe(
        self,
        db_session,
        generation_setup,
        healthy_embeddings,
        advance_generation,
        make_model,
        make_inference_endpoint,
    ):
        from printstash_core.search.text_inputs import TextRecipe

        from app.db.models import IndexGeneration
        from app.modules.search import generations
        from app.schemas.search_generations import GenerationProposal

        actor, endpoint = generation_setup
        model = make_model("Original human title")
        old = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", passage_recipe_version=1
            ),
        )
        advance_generation(old.id)
        chat = make_inference_endpoint(kind="chat", supports_images=True)
        configuration.update(
            db_session,
            SearchSettings(
                enabled=True,
                captions_enabled=True,
                send_rendered_images=True,
                chat_endpoint_id=chat.id,
            ),
            actor_id=actor.id,
        )
        captions.patch(
            db_session,
            actor,
            SearchSubject(SubjectType.MODEL, model.id),
            CaptionPatch(action="edit", text="Zygomatic mount"),
        )
        assert generations.ensure_caption_recipe(db_session)
        candidates = db_session.exec(
            select(IndexGeneration).order_by(IndexGeneration.id)
        ).all()
        assert len(candidates) == 2
        assert (
            db_session.get(IndexGeneration, old.id, populate_existing=True).state
            == "active"
        )
        new = candidates[-1]
        assert (
            TextRecipe.for_space(generations.contract(db_session, new)).passage_version
            == 2
        )
        from app.db.projections import bind_content_projection
        from app.modules.library.commands import update_model
        from app.modules.search.projection import LibraryProjection
        from app.schemas.models import ModelUpdate

        previous_projection = bind_content_projection(LibraryProjection())
        try:
            update_model(
                model.id, ModelUpdate(name="Changed human title"), actor, db_session
            )
        finally:
            bind_content_projection(previous_projection)
        from tests.search_projection import drain_search
        drain_search(db_session)
        coexisting = db_session.exec(
            select(SearchPassage)
            .where(
                SearchPassage.subject_type == "model",
                SearchPassage.subject_id == model.id,
            )
            .order_by(SearchPassage.recipe_version)
        ).all()
        assert [row.recipe_version for row in coexisting] == [1, 2]
        assert all("Changed human title" in row.text for row in coexisting)
        assert "Zygomatic" not in coexisting[0].text
        assert "Zygomatic" in coexisting[1].text
        advance_generation(new.id)
        db_session.expire_all()
        assert db_session.get(IndexGeneration, new.id).state == "active"
        assert db_session.get(IndexGeneration, old.id).state == "retired"
        assert any(
            "Zygomatic" in text
            for request in healthy_embeddings.requests
            for text in request["input"]
        )
        assert not generations.ensure_caption_recipe(db_session)

    def test_keeps_one_task_for_repeated_generation_requests(
        self, db_session, caption_setup
    ):
        actor, subject, _, _ = caption_setup
        first = captions.patch(
            db_session, actor, subject, CaptionPatch(action="generate")
        )
        second = captions.patch(
            db_session, actor, subject, CaptionPatch(action="generate")
        )
        assert first.version_token == second.version_token
        assert len(db_session.exec(select(SubjectCaption)).all()) == 1

    def test_keeps_dismissal_when_the_endpoint_changes(
        self, db_session, caption_setup, make_inference_endpoint
    ):
        from app.modules.inference.endpoint import EndpointConfig

        actor, subject, _, _ = caption_setup
        captions.patch(db_session, actor, subject, CaptionPatch(action="dismiss"))
        endpoint = make_inference_endpoint(
            kind="chat",
            supports_images=True,
            config=EndpointConfig(
                base_url="http://replacement.test/v1", model="replacement-vlm"
            ),
        )
        configuration.update(
            db_session,
            SearchSettings(
                enabled=True,
                captions_enabled=True,
                send_rendered_images=True,
                chat_endpoint_id=endpoint.id,
            ),
            actor_id=actor.id,
        )
        assert sweep(db_session) == 0
        assert captions.lookup(db_session, subject).state == "dismissed"

    def test_invalidates_generated_claims_when_geometry_changes(
        self, db_session, caption_setup
    ):
        actor, subject, file, _ = caption_setup
        provider = CaptionProvider()
        CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=rendered_preview,
        ).work_one()
        db_session.expire_all()
        file.sha256 = "7" * 64
        db_session.add(file)
        sync_subject(db_session, subject)
        db_session.commit()
        assert captions.read(db_session, actor, subject).text == ""
        assert (
            db_session.exec(
                ordered_passages(db_session, "flanged", select(SearchPassage.id))
            ).all()
            == []
        )

    def test_bounds_remote_timeout_retries(self, db_session, caption_setup):
        actor, subject, _, _ = caption_setup
        provider = CaptionProvider(error_code="inference_timeout")
        processor = CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=rendered_preview,
        )
        for _ in range(3):
            assert processor.work_one()
            db_session.expire_all()
            row = captions.lookup(db_session, subject)
            row.retry_after = utcnow() - timedelta(seconds=1)
            db_session.add(row)
            db_session.commit()
        assert not processor.work_one()
        result = captions.read(db_session, actor, subject)
        assert (result.phase, result.error_code, result.text) == (
            "failed",
            "inference_timeout",
            "",
        )
        assert len(provider.requests) == 3

    def test_finishes_an_exhausted_lease_after_restart(self, db_session, caption_setup):
        actor, subject, _, _ = caption_setup
        captions.patch(db_session, actor, subject, CaptionPatch(action="generate"))
        row = captions.lookup(db_session, subject)
        row.phase, row.attempts, row.lease_token = "running", 3, "a" * 32
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db_session.add(row)
        db_session.commit()
        provider = CaptionProvider()
        assert not CaptionProcessor(
            get_session_factory(),
            provider_factory=lambda *_: provider,
            image_renderer=rendered_preview,
        ).work_one()
        db_session.expire_all()
        result = captions.read(db_session, actor, subject)
        assert (result.phase, result.error_code) == (
            "failed",
            "caption_attempts_exhausted",
        )
        assert provider.requests == []
