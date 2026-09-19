"""Real local visual generations preserve source, lifecycle and reuse boundaries."""

import hashlib
import io
from pathlib import Path

import pytest
from sqlmodel import select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import FileType, IndexGeneration, PassageVector, SearchIndexFailure
from app.db.session import get_session_factory
from app.modules.inference import model_cache
from app.modules.search import configuration, generations, indexing, visual_index
from app.schemas.inference import SearchSettings
from app.schemas.search_generations import GenerationProposal
from tests.factories.embeddings import local_embedding_assets
from tests.factories.geometry import tetrahedron


@pytest.fixture
def visual_setup(db_session, tmp_path, monkeypatch, make_user, make_model, make_file):
    directory = local_embedding_assets(tmp_path / "cache" / "clip")
    monkeypatch.setitem(_overlay, "embedding_cache_dir", directory.parent)
    monkeypatch.setitem(_overlay, "embedding_local_model_dir", "")
    actor = make_user(superuser=True)
    model = make_model("Unnamed model")
    payload = tetrahedron().export(file_type="stl")
    path = tmp_path / "part.stl"
    path.write_bytes(payload)
    file = make_file(
        model,
        file_type=FileType.STL,
        path=str(path),
        sha256=hashlib.sha256(payload).hexdigest(),
        external=True,
        size_bytes=len(payload),
    )
    configuration.update(
        db_session, SearchSettings(enabled=True, local_models_enabled=True)
    )
    db_session.commit()
    return actor, model_cache.inspect(directory), model, file


@pytest.fixture
def cached_preview(make_model, make_file, make_thumbnail_generation):
    from PIL import Image
    from printstash_core.search.visual_inputs import VisualRecipe

    from app.db.models import ThumbnailGenerationState
    from app.modules.storage.storage_backend.runtime import get_backend

    file = make_file(make_model(), file_type=FileType.STL)
    recipe = VisualRecipe("c" * 64, 32, "thumbnail")
    stream = io.BytesIO()
    Image.new("RGB", (640, 480), "red").save(stream, "WEBP", lossless=True)
    payload = stream.getvalue()
    backend = get_backend()
    key = backend.thumbnail_variant_key(file.id, file.sha256, recipe.thumbnail_recipe)
    backend.write_bytes(payload, key)
    row = make_thumbnail_generation(
        file,
        recipe_fingerprint=recipe.thumbnail_recipe,
        state=ThumbnailGenerationState.READY,
        storage_key=key,
        output_sha256=hashlib.sha256(payload).hexdigest(),
        output_size_bytes=len(payload),
        width=640,
        height=480,
        strategy="full",
        complete=True,
    )
    return file, recipe, row, backend


def proposal(model, **kwargs):
    return GenerationProposal(
        local_model_id=model.id, index_backend="numpy", profile="multiview", **kwargs
    )


class TestVisualIndex:
    def test_ignores_visual_legs_for_nonmodel_queries(
        self, db_session, visual_setup, advance_generation
    ):
        from printstash_core.search.passages import SubjectType

        from app.modules.search import semantic, visual_query

        actor, encoder, _, _ = visual_setup
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)
        leg = next(
            leg
            for leg in semantic.registry(db_session, configuration.settings(db_session))
            if leg.name == "multiview"
        )

        result = visual_query.retrieve(
            db_session,
            actor.id,
            actor.auth_version,
            "bracket",
            leg,
            types=(SubjectType.DOCUMENT,),
        )

        assert result.available is True
        assert result.visual_matches == ()

    def test_refuses_remote_endpoints_for_visual_input(
        self, db_session, visual_setup, advance_generation
    ):
        from dataclasses import replace

        from printstash_core.search.passages import SubjectType

        from app.modules.search import semantic, visual_query

        actor, encoder, _, _ = visual_setup
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)
        leg = next(
            leg
            for leg in semantic.registry(db_session, configuration.settings(db_session))
            if leg.name == "multiview"
        )
        leg = replace(leg, space=replace(leg.space, provider="openai_compatible"))

        result = visual_query.retrieve(
            db_session,
            actor.id,
            actor.auth_version,
            "bracket",
            leg,
            types=tuple(SubjectType),
        )

        assert result.available is False
        assert result.error_code == "embedding_image_unavailable"

    def test_refuses_incomplete_visual_worker_batches(self, db_session, visual_setup):
        from printstash_core.inference import EmbeddingError

        actor, encoder, _, _ = visual_setup
        generations.prepare(db_session, actor, proposal(encoder))
        generation_id, token = indexing.claim(db_session)
        source = visual_index.pending(
            db_session, db_session.get(IndexGeneration, generation_id)
        )

        with pytest.raises(EmbeddingError, match="embedding_view_count_mismatch"):
            visual_index.publish(
                db_session,
                generation_id,
                token,
                source,
                ((1.0, 0, 0),) * 5,
                (1.0, 0, 0),
            )

        assert db_session.exec(select(PassageVector)).all() == []

    def test_discards_stale_visual_worker_failures(self, db_session, visual_setup):
        actor, encoder, _, file = visual_setup
        generations.prepare(db_session, actor, proposal(encoder))
        generation_id, token = indexing.claim(db_session)
        source = visual_index.pending(
            db_session, db_session.get(IndexGeneration, generation_id)
        )
        file.sha256 = "e" * 64
        db_session.add(file)
        db_session.commit()

        visual_index.record_failure(
            db_session, generation_id, token, source, "embedding_render_failed"
        )

        assert db_session.exec(select(SearchIndexFailure)).all() == []

    def test_sanitizes_visual_failure_details(self, db_session, visual_setup):
        actor, encoder, _, _ = visual_setup
        generations.prepare(db_session, actor, proposal(encoder))
        generation_id, token = indexing.claim(db_session)
        source = visual_index.pending(
            db_session, db_session.get(IndexGeneration, generation_id)
        )

        visual_index.record_failure(
            db_session, generation_id, token, source, "/private/render-path"
        )

        assert (
            db_session.exec(select(SearchIndexFailure)).one().error_code
            == "embedding_render_failed"
        )

    def test_retrieves_visual_matches_from_a_valid_image_upload(
        self, client, db_session, visual_setup, advance_generation
    ):
        from PIL import Image

        from tests.factories import bearer

        actor, encoder, model, _ = visual_setup
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)
        body = io.BytesIO()
        Image.new("RGB", (32, 32), "gray").save(body, "PNG")

        response = client.post(
            "/api/v1/search/image",
            headers=bearer(actor) | {"Content-Type": "image/png"},
            content=body.getvalue(),
        )

        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["items"][0]["subject_id"] == model.id
        assert response.json()["items"][0]["evidence"]

    def test_respects_an_independent_consumers_render_permit(
        self, db_session, visual_setup, monkeypatch
    ):
        from printstash_core.inference import EmbeddingError
        from printstash_core.inference.context import InferenceContext
        from printstash_core.search.visual_inputs import VisualRecipe

        from app.db.models import ThumbnailRenderSlot
        from app.modules.media import compute_slots
        from app.modules.media.thumbnail_generations import (
            ThumbnailEnsureOutcome,
            ensure_thumbnail,
        )

        _, encoder, _, file = visual_setup
        monkeypatch.setitem(_overlay, "max_render_jobs", 1)
        permit = compute_slots.acquire(db_session, "independent-consumer")
        assert permit is not None
        recipe = VisualRecipe(encoder.id, 32, "multiview")
        try:
            thumbnail = ensure_thumbnail(db_session, file)
            assert thumbnail.outcome == ThumbnailEnsureOutcome.COALESCED
            with pytest.raises(EmbeddingError, match="embedding_compute_busy"):
                visual_index.render(
                    get_session_factory(),
                    file,
                    recipe,
                    InferenceContext.bounded(2, priority="background"),
                )
            slots = db_session.exec(select(ThumbnailRenderSlot)).all()
            assert [(slot.id, slot.lease_token) for slot in slots] == [
                (permit.id, "independent-consumer")
            ]
        finally:
            compute_slots.release(db_session, permit.id, "independent-consumer")
            db_session.commit()

        result = visual_index.render(
            get_session_factory(),
            file,
            recipe,
            InferenceContext.bounded(20),
        )
        assert len(result.views) == 6
        assert len(result.thumbnail.rgb) == 32 * 32 * 3
        db_session.expire_all()
        assert db_session.exec(select(ThumbnailRenderSlot)).one().lease_token is None

    def test_corrupt_view_identity_does_not_count_as_complete(
        self, db_session, visual_setup, advance_generation
    ):
        actor, encoder, model, file = visual_setup
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)
        row = db_session.exec(
            select(PassageVector).where(
                PassageVector.generation_id == generation.id,
                PassageVector.unit_key == f"file:{file.id}:view:0",
            )
        ).one()
        row.unit_key = f"file:{file.id}:view:99"
        db_session.add(row)
        db_session.commit()
        stored = db_session.get(IndexGeneration, generation.id)
        assert stored is not None
        assert generations.counts(db_session, stored) == (1, 0, 0)

    @pytest.mark.parametrize(
        "changed", [None, "source", "recipe", "strategy", "digest"]
    )
    def test_reuses_only_verified_current_mesh_thumbnails(
        self, db_session, visual_setup, tmp_path, monkeypatch, changed
    ):
        from PIL import Image
        from printstash_core.inference.context import InferenceContext
        from printstash_core.search.visual_inputs import VisualRecipe

        from app.db.models import ThumbnailGeneration, ThumbnailGenerationState
        from app.modules.storage.storage_backend.local import LocalStorageBackend

        actor, encoder, model, file = visual_setup
        recipe = VisualRecipe(encoder.id, 32, "thumbnail")
        body = io.BytesIO()
        Image.new("RGB", (640, 480), "red").save(body, "WEBP", lossless=True)
        data = body.getvalue()
        (tmp_path / "stored").mkdir()
        (tmp_path / "thumbs").mkdir()
        backend = LocalStorageBackend(
            data_dir=tmp_path / "stored", thumb_dir=tmp_path / "thumbs"
        )
        key = str(tmp_path / "stored" / "thumbnail.webp")
        Path(key).write_bytes(data)
        monkeypatch.setattr(visual_index, "get_backend", lambda: backend)
        row = ThumbnailGeneration(
            file_id=file.id,
            source_sha256=file.sha256,
            recipe_fingerprint=recipe.thumbnail_recipe,
            state=ThumbnailGenerationState.READY,
            storage_key=key,
            output_sha256=hashlib.sha256(data).hexdigest(),
            output_size_bytes=len(data),
            width=640,
            height=480,
            strategy="full",
            complete=True,
        )
        if changed == "source":
            row.source_sha256 = "f" * 64
        if changed == "recipe":
            row.recipe_fingerprint = "another-recipe"
        if changed == "strategy":
            row.strategy = "embedded"
        if changed == "digest":
            row.output_sha256 = "f" * 64
        db_session.add(row)
        db_session.commit()
        result = visual_index.cached_thumbnail(
            db_session, file, recipe, InferenceContext.bounded(10)
        )
        if changed is None:
            assert result.rgb == bytes([255, 0, 0]) * 32 * 32
        else:
            assert result is None

    def test_visual_cutover_leaves_active_text_generation_unchanged(
        self, db_session, visual_setup, advance_generation, tmp_path
    ):
        from printstash_core.search.passages import SearchSubject, SubjectType

        from app.modules.search.passages import sync_subject
        from tests.factories.embeddings import text_embedding_assets

        actor, encoder, model, file = visual_setup
        text = model_cache.inspect(text_embedding_assets(tmp_path / "cache" / "text"))
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, model.id))
        db_session.commit()
        text_generation = generations.prepare(
            db_session,
            actor,
            GenerationProposal(local_model_id=text.id, index_backend="numpy"),
        )
        advance_generation(text_generation.id)
        before = db_session.exec(
            select(PassageVector.id).where(
                PassageVector.generation_id == text_generation.id
            )
        ).all()
        assert before
        visual = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(visual.id)
        db_session.expire_all()
        assert db_session.get(IndexGeneration, text_generation.id).state == "active"
        assert (
            db_session.exec(
                select(PassageVector.id).where(
                    PassageVector.generation_id == text_generation.id
                )
            ).all()
            == before
        )
        assert db_session.get(IndexGeneration, visual.id).state == "active"

    def test_rejects_artifact_changed_during_native_render(
        self, visual_setup, monkeypatch
    ):
        from printstash_core.inference import EmbeddingError
        from printstash_core.inference.context import InferenceContext
        from printstash_core.search.visual_inputs import VisualRecipe

        actor, encoder, model, file = visual_setup
        recipe = VisualRecipe(encoder.id, 32, "thumbnail")
        original = visual_index.visual_render.render

        def changed(path, **kwargs):
            result = original(path, **kwargs)
            path.write_bytes(path.read_bytes() + b"changed")
            return result

        monkeypatch.setattr(visual_index.visual_render, "render", changed)
        with pytest.raises(EmbeddingError, match="embedding_source_changed"):
            visual_index.render(
                get_session_factory(), file, recipe, InferenceContext.bounded(20)
            )

    def test_prepares_an_independent_thumbnail_fallback(
        self, db_session, visual_setup, advance_generation, monkeypatch
    ):
        from app.db.models import EmbeddingSpace

        actor, encoder, model, file = visual_setup
        first = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(first.id)
        fallback = db_session.exec(
            select(IndexGeneration)
            .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
            .where(EmbeddingSpace.profile == "thumbnail")
        ).one()
        advance_generation(fallback.id)
        db_session.expire_all()
        assert db_session.get(IndexGeneration, fallback.id).state == "active"
        # Native multiview v2 and media-thumbnail v1 are distinct recipes.
        # The fallback must serve its own verified vectors, not copy v2 output.
        assert db_session.get(IndexGeneration, fallback.id).copied == 0

        from printstash_core.inference import EmbeddingError

        from app.modules.search import visual_query
        from app.modules.search.retrieval import search

        original = visual_query.embedding_provider

        def unavailable_multiview(session, space):
            if space.profile == "multiview":
                raise EmbeddingError("embedding_runtime_unavailable")
            return original(session, space)

        monkeypatch.setattr(visual_query, "embedding_provider", unavailable_multiview)
        result = search(db_session, actor, "gray")
        assert result.items[0].subject_id == model.id
        assert [reason.leg for reason in result.items[0].evidence] == ["thumbnail"]
        assert result.leg_errors == {"multiview": "embedding_runtime_unavailable"}

    def test_reuses_native_views_after_an_aggregation_change(
        self, db_session, visual_setup, advance_generation, monkeypatch
    ):
        actor, encoder, model, file = visual_setup
        first = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(first.id)
        db_session.expire_all()
        assert generations.counts(
            db_session, db_session.get(IndexGeneration, first.id)
        ) == (1, 1, 0)
        rows = db_session.exec(
            select(PassageVector).where(PassageVector.generation_id == first.id)
        ).all()
        assert {row.unit_kind for row in rows} == {
            "visual_view",
            "visual_mean",
            "visual_thumbnail",
        }
        assert len([row for row in rows if row.unit_kind == "visual_view"]) == 6
        assert all(
            row.subject_id == model.id
            and row.file_id == file.id
            and row.passage_id is None
            for row in rows
        )

        def never_render(*args, **kwargs):
            raise AssertionError("unchanged views must be reused")

        monkeypatch.setattr(visual_index, "render", never_render)
        second = generations.prepare(
            db_session, actor, proposal(encoder, aggregation="max")
        )
        advance_generation(second.id)
        db_session.expire_all()
        assert db_session.get(IndexGeneration, first.id).state == "retired"
        assert db_session.get(IndexGeneration, second.id).copied == 1

    @pytest.mark.parametrize("mutation", ["hash", "trash", "cancel"])
    def test_rejects_late_visual_publication(self, db_session, visual_setup, mutation):
        actor, encoder, model, file = visual_setup
        generation = generations.prepare(db_session, actor, proposal(encoder))
        generation_id, token = indexing.claim(db_session)
        source = visual_index.pending(
            db_session, db_session.get(IndexGeneration, generation_id)
        )
        if mutation == "hash":
            file.sha256 = "e" * 64
            db_session.add(file)
        elif mutation == "trash":
            model.deleted_at = utcnow()
            db_session.add(model)
        else:
            generations.cancel(db_session, generation_id, generation.version_token)
        db_session.commit()
        assert not visual_index.publish(
            db_session, generation_id, token, source, ((1.0, 0, 0),) * 6, (1.0, 0, 0)
        )
        assert (
            db_session.exec(
                select(PassageVector.id).where(
                    PassageVector.generation_id == generation_id
                )
            ).all()
            == []
        )

    def test_quarantines_incomplete_geometry_without_publishing_partial_views(
        self, db_session, visual_setup
    ):
        actor, encoder, model, file = visual_setup
        path = Path(file.path)
        path.write_bytes(b"invalid geometry")
        file.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        db_session.add(file)
        db_session.commit()
        generation = generations.prepare(db_session, actor, proposal(encoder))
        processor = indexing.IndexProcessor(get_session_factory())
        for _ in range(15):
            processor.work_one()
            db_session.expire_all()
            for failure in db_session.exec(select(SearchIndexFailure)).all():
                failure.retry_after = utcnow()
                db_session.add(failure)
            db_session.commit()
        failures = db_session.exec(
            select(SearchIndexFailure).where(
                SearchIndexFailure.generation_id == generation.id
            )
        ).all()
        assert len(failures) == 1
        assert failures[0].file_id == file.id and failures[0].state == "quarantined"
        assert db_session.exec(select(PassageVector.id)).all() == []


class TestVisualQuery:
    @pytest.mark.parametrize("change", ["hash", "trash"])
    def test_model_query_rechecks_source_after_neighbor_search(
        self, db_session, visual_setup, advance_generation, client, monkeypatch, change
    ):
        from app.modules.search import vector_store
        from tests.factories import bearer

        actor, encoder, source, file = visual_setup
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)
        original = vector_store.query

        def changed(session, **kwargs):
            result = original(session, **kwargs)
            if change == "hash":
                file.sha256 = "f" * 64
                db_session.add(file)
            else:
                source.deleted_at = utcnow()
                db_session.add(source)
            db_session.commit()
            return result

        monkeypatch.setattr(vector_store, "query", changed)
        response = client.get(
            f"/api/v1/models/{source.id}/similar-text", headers=bearer(actor)
        )
        assert response.status_code == (409 if change == "hash" else 404), response.text
        assert "items" not in response.json()

    def test_model_query_cursor_expires_when_source_changes(
        self,
        db_session,
        visual_setup,
        advance_generation,
        client,
        make_model,
        make_file,
    ):
        from tests.factories import bearer

        actor, encoder, source, file = visual_setup
        for number in range(2):
            other = make_model(f"Related {number}")
            make_file(
                other,
                file_type=FileType.STL,
                path=file.path,
                sha256=file.sha256,
                external=True,
                size_bytes=file.size_bytes,
            )
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)
        url = f"/api/v1/models/{source.id}/similar-text"
        response = client.get(url, headers=bearer(actor), params={"limit": 1})
        assert response.status_code == 200, response.text
        cursor = response.json()["next_cursor"]
        assert cursor
        file.sha256 = "f" * 64
        db_session.add(file)
        db_session.commit()
        response = client.get(
            url, headers=bearer(actor), params={"limit": 1, "cursor": cursor}
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "search_cursor_invalid"

    def test_model_query_returns_other_models_from_current_vectors(
        self,
        db_session,
        visual_setup,
        advance_generation,
        client,
        make_model,
        make_file,
        monkeypatch,
    ):
        from app.modules.inference.local import LocalEmbeddingProvider
        from tests.factories import bearer

        actor, encoder, source, file = visual_setup
        other = make_model("Other unnamed model")
        make_file(
            other,
            file_type=FileType.STL,
            path=file.path,
            sha256=file.sha256,
            external=True,
            size_bytes=file.size_bytes,
        )
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)

        def no_inference(*args, **kwargs):
            raise AssertionError("Model-as-query must reuse current native vectors")

        monkeypatch.setattr(LocalEmbeddingProvider, "embed", no_inference)

        response = client.get(
            f"/api/v1/models/{source.id}/similar-text", headers=bearer(actor)
        )
        assert response.status_code == 200, response.text
        assert [item["subject_id"] for item in response.json()["items"]] == [other.id]
        assert response.headers["cache-control"] == "no-store"

    def test_model_query_schedules_missing_work_without_creating_more_generations(
        self,
        db_session,
        visual_setup,
        advance_generation,
        make_model,
        make_file,
        client,
    ):
        from tests.factories import bearer

        actor, encoder, source, file = visual_setup
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)
        added = make_model("New source")
        make_file(
            added,
            file_type=FileType.STL,
            path=file.path,
            sha256=file.sha256,
            external=True,
            size_bytes=file.size_bytes,
        )
        before = db_session.exec(select(IndexGeneration.id)).all()
        response = client.get(
            f"/api/v1/models/{added.id}/similar-text", headers=bearer(actor)
        )
        assert response.status_code == 200, response.text
        assert "search_model_index_pending" in response.json()["leg_errors"].values()
        assert db_session.exec(select(IndexGeneration.id)).all() == before
        db_session.expire_all()
        assert db_session.get(IndexGeneration, generation.id).phase == "backfill"

    @pytest.mark.parametrize("multipart", [False, True])
    def test_searches_uploaded_images_without_temporary_files_or_query_cache(
        self,
        db_session,
        visual_setup,
        advance_generation,
        client,
        monkeypatch,
        multipart,
    ):
        import tempfile

        from PIL import Image

        from app.modules.inference.query import runner
        from tests.factories import bearer

        actor, encoder, model, file = visual_setup
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)
        image = io.BytesIO()
        Image.new("RGB", (32, 16), "gray").save(image, "PNG")
        before_vectors = db_session.exec(select(PassageVector.id)).all()
        before_cache = dict(runner()._cache)
        from app.modules.search import visual_query

        expected_factory = get_session_factory()
        actual_provider = visual_query.embedding_provider

        def scoped_provider(session, space):
            assert get_session_factory() is expected_factory, (
                "request database context lost in executor"
            )
            return actual_provider(session, space)

        monkeypatch.setattr(visual_query, "embedding_provider", scoped_provider)

        def no_temp(*args, **kwargs):
            raise AssertionError("query image must stay in memory")

        for name in (
            "TemporaryFile",
            "SpooledTemporaryFile",
            "NamedTemporaryFile",
            "mkstemp",
        ):
            monkeypatch.setattr(tempfile, name, no_temp)
        headers = bearer(actor)
        kwargs = (
            {"files": {"image": ("private-query.png", image.getvalue(), "image/png")}}
            if multipart
            else {
                "content": image.getvalue(),
                "headers": {**headers, "Content-Type": "image/png"},
            }
        )
        if multipart:
            kwargs["headers"] = headers
        response = client.post("/api/v1/search/image", **kwargs)

        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        result = response.json()
        assert result["items"], result
        assert result["items"][0]["subject_id"] == model.id
        assert result["items"][0]["evidence"] == [
            {"leg": "multiview", "field": "visual", "text": "", "ranges": []}
        ]
        assert "private-query" not in response.text
        assert dict(runner()._cache) == before_cache
        db_session.expire_all()
        assert db_session.exec(select(PassageVector.id)).all() == before_vectors

    def test_text_query_uses_the_paired_tower_without_needing_a_text_generation(
        self, db_session, visual_setup, advance_generation
    ):
        from app.modules.search.retrieval import search

        actor, encoder, model, file = visual_setup
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)
        result = search(db_session, actor, "gray")
        assert result.items[0].subject_id == model.id
        assert result.items[0].evidence[0].leg == "multiview"
        assert "semantic_text" not in result.legs

    def test_denies_hidden_images_before_inference(
        self,
        db_session,
        visual_setup,
        advance_generation,
        make_user,
        make_collection,
        monkeypatch,
    ):
        from app.modules.inference.local import LocalEmbeddingProvider
        from app.modules.search.retrieval import search

        actor, encoder, model, file = visual_setup
        private = make_collection("Private")
        model.collection_id = private.id
        db_session.add(model)
        db_session.commit()
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)
        viewer = make_user()

        def denied(*args, **kwargs):
            raise AssertionError("hidden geometry must not reach inference")

        monkeypatch.setattr(LocalEmbeddingProvider, "embed", denied)
        assert search(db_session, viewer, "gray").items == []


class TestFilteredVisualQuery:
    def test_applies_actual_history_to_visual_results(
        self, db_session, visual_setup, advance_generation, make_print_job
    ):
        from app.db.models import PrintJobState
        from app.modules.search.retrieval import search
        from app.schemas.models import ModelFilters

        actor, encoder, model, file = visual_setup
        generation = generations.prepare(db_session, actor, proposal(encoder))
        advance_generation(generation.id)
        assert (
            search(
                db_session,
                actor,
                "gray",
                filters=ModelFilters(print_duration_max_s=200),
            ).items
            == []
        )
        make_print_job(
            file,
            state=PrintJobState.COMPLETED,
            actual_duration_s=199,
            finished_at=utcnow(),
        )
        result = search(
            db_session, actor, "gray", filters=ModelFilters(print_duration_max_s=200)
        )
        assert [item.subject_id for item in result.items] == [model.id]
        assert any(evidence.leg == "multiview" for evidence in result.items[0].evidence)


class TestCachedThumbnail:
    def test_renders_from_a_verified_cached_preview(self, cached_preview):
        from printstash_core.inference.context import InferenceContext

        file, recipe, _, _ = cached_preview
        result = visual_index.render(
            get_session_factory(), file, recipe, InferenceContext.bounded(10)
        )

        assert result.thumbnail.rgb == bytes([255, 0, 0]) * 32 * 32
        assert result.views == (result.thumbnail,)

    def test_rejects_preview_larger_than_its_receipt(self, db_session, cached_preview):
        from printstash_core.inference.context import InferenceContext

        file, recipe, row, _ = cached_preview
        row.output_size_bytes -= 1
        db_session.add(row)
        db_session.commit()

        result = visual_index.cached_thumbnail(
            db_session, file, recipe, InferenceContext.bounded(10)
        )

        assert result is None

    def test_rejects_missing_cached_object(self, db_session, cached_preview):
        from printstash_core.inference.context import InferenceContext

        file, recipe, row, _ = cached_preview
        Path(row.storage_key).unlink()

        result = visual_index.cached_thumbnail(
            db_session, file, recipe, InferenceContext.bounded(10)
        )

        assert result is None
