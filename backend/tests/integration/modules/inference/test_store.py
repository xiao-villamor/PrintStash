"""Native vectors are reusable across restarts and fenced by run, source and EDIT permissions."""

from dataclasses import replace

import pytest
from printstash_core.inference import EmbeddingError
from sqlmodel import select

from app.core.time import utcnow
from app.db.models import CollectionRole, EmbeddingSpace, IndexGeneration, PassageVector
from app.db.session import get_session_factory
from app.modules.inference import store
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.similarity import configuration, runs
from tests.factories.embeddings import local_embedding_assets


@pytest.fixture
def index(db_session, tmp_path, make_user):
    directory = local_embedding_assets(tmp_path / "assets")
    provider = LocalEmbeddingProvider(
        get_session_factory(), directory, "two-tower-contract", 1
    )
    generation_id = store.initialize(get_session_factory(), provider)
    actor = make_user(superuser=True)
    configuration.update_settings(
        db_session, actor, {"enabled": True, "embeddings_enabled": True}
    )
    runs.start(db_session, actor)
    run, token = runs.claim(db_session)
    run.state = "running"
    db_session.add(run)
    db_session.commit()
    return provider, generation_id, actor, run, token


class TestNativeStore:
    def test_initialization_reuses_immutable_rows(self, db_session, index):
        provider, generation_id, *_ = index
        assert store.initialize(get_session_factory(), provider) == generation_id
        assert len(db_session.exec(select(EmbeddingSpace)).all()) == 1
        assert len(db_session.exec(select(IndexGeneration)).all()) == 1
        assert (
            store.active_generation(db_session, replace(provider.space, dimension=4))
            is None
        )

    def test_indexes_distinct_component_inputs(
        self, db_session, index, make_model, make_file
    ):
        provider, generation_id, actor, run, token = index
        model = make_model()
        first, second = make_file(model), make_file(model)
        for file, component in [(first, 0), (first, 1), (second, 0)]:
            assert store.publish(
                db_session,
                actor,
                generation_id=generation_id,
                space=provider.space,
                file_id=file.id,
                component_index=component,
                input_hash=file.sha256,
                vector=[1, 0, 0],
                run_id=run.id,
                lease_token=token,
            )
        rows = db_session.exec(select(PassageVector)).all()
        assert len({row.unit_key for row in rows}) == 3
        assert sorted(store.unit_component(row.unit_key) for row in rows) == [0, 0, 1]
        assert all(len(row.vector_blob) == 12 for row in rows)

    def test_replayed_unit_is_idempotent(
        self, db_session, index, make_model, make_file
    ):
        provider, generation_id, actor, run, token = index
        file = make_file(make_model())
        arguments = dict(
            generation_id=generation_id,
            space=provider.space,
            file_id=file.id,
            component_index=0,
            input_hash=file.sha256,
            vector=[1, 0, 0],
            run_id=run.id,
            lease_token=token,
        )
        assert store.publish(db_session, actor, **arguments)
        assert store.publish(db_session, actor, **arguments) is False
        assert len(db_session.exec(select(PassageVector)).all()) == 1

    @pytest.mark.parametrize("fence", ["source", "lease", "cancel", "trash", "queued"])
    def test_discards_fenced_publication(
        self, db_session, index, make_model, make_file, fence
    ):
        provider, generation_id, actor, run, token = index
        file = make_file(make_model())
        source_hash = file.sha256
        if fence == "source":
            file.sha256 = "f" * 64
        elif fence == "lease":
            run.lease_token = "successor"
        elif fence == "cancel":
            run.cancel_requested = True
        elif fence == "queued":
            run.state = "queued"
        else:
            file.deleted_at = utcnow()
        db_session.add_all([file, run])
        db_session.commit()
        assert (
            store.publish(
                db_session,
                actor,
                generation_id=generation_id,
                space=provider.space,
                file_id=file.id,
                component_index=0,
                input_hash=source_hash,
                vector=[1, 0, 0],
                run_id=run.id,
                lease_token=token,
            )
            is False
        )
        assert db_session.exec(select(PassageVector)).all() == []

    def test_search_hides_uneditable_models(
        self,
        db_session,
        index,
        make_model,
        make_file,
        make_user,
        make_collection,
        grant_role,
    ):
        provider, generation_id, actor, run, token = index
        visible, hidden = (
            make_collection(path="visible"),
            make_collection(path="hidden"),
        )
        editor = make_user()
        grant_role(editor, visible, CollectionRole.EDIT)
        models = [make_model(collection=visible), make_model(collection=hidden)]
        for model in models:
            file = make_file(model)
            store.publish(
                db_session,
                actor,
                generation_id=generation_id,
                space=provider.space,
                file_id=file.id,
                component_index=0,
                input_hash=file.sha256,
                vector=[1, 0, 0],
                run_id=run.id,
                lease_token=token,
            )
        result = store.query(
            db_session,
            editor,
            generation_id=generation_id,
            space=provider.space,
            vector=[1, 0, 0],
        )
        assert [item.subject_id for item in result.items] == [models[0].id]
        assert result.scanned == 1

    def test_search_excludes_changed_inputs(
        self, db_session, index, make_model, make_file
    ):
        provider, generation_id, actor, run, token = index
        file = make_file(make_model())
        store.publish(
            db_session,
            actor,
            generation_id=generation_id,
            space=provider.space,
            file_id=file.id,
            component_index=0,
            input_hash=file.sha256,
            vector=[1, 0, 0],
            run_id=run.id,
            lease_token=token,
        )
        file.sha256 = "f" * 64
        db_session.add(file)
        db_session.commit()
        assert (
            store.query(
                db_session,
                actor,
                generation_id=generation_id,
                space=provider.space,
                vector=[1, 0, 0],
            ).items
            == ()
        )

    def test_refuses_cross_space_publication(
        self, db_session, index, make_file, make_model
    ):
        provider, generation_id, actor, run, token = index
        file = make_file(make_model())
        with pytest.raises(EmbeddingError, match="space_mismatch"):
            store.publish(
                db_session,
                actor,
                generation_id=generation_id,
                space=replace(provider.space, model_revision="different"),
                file_id=file.id,
                component_index=0,
                input_hash=file.sha256,
                vector=[1, 0, 0],
                run_id=run.id,
                lease_token=token,
            )


class TestGenerationLifecycle:
    def test_refuses_implicit_model_replacement(self, db_session, index):
        import json

        provider, old_id, *_ = index
        path = provider.directory / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["model_revision"] = "next-export"
        path.write_text(json.dumps(manifest))
        successor = LocalEmbeddingProvider(
            get_session_factory(), provider.directory, provider.model_key, 1
        )
        with pytest.raises(
            EmbeddingError, match="embedding_active_generation_conflict"
        ):
            store.initialize(get_session_factory(), successor)
        assert len(db_session.exec(select(EmbeddingSpace)).all()) == 1
        assert db_session.exec(select(IndexGeneration)).one().id == old_id

    def test_reuses_space_after_explicit_generation_retirement(self, db_session, index):
        provider, old_id, *_ = index
        previous = db_session.get(IndexGeneration, old_id)
        space_id = previous.space_id
        previous.state = "retired"
        previous.active_profile_key = None
        db_session.add(previous)
        db_session.commit()
        new_id = store.initialize(get_session_factory(), provider)
        assert new_id != old_id
        assert db_session.get(IndexGeneration, new_id).space_id == space_id
        assert len(db_session.exec(select(EmbeddingSpace)).all()) == 1
        db_session.refresh(previous)
        assert previous.state == "retired"

    def test_adopts_persisted_native_vectors_through_shared_contract(
        self, db_session, index, make_model, make_file, make_passage_vector
    ):
        import json
        import struct

        from printstash_core.inference import EmbeddingSpace as SpaceContract
        from sqlmodel import Session

        provider, generation_id, actor, *_ = index
        generation = db_session.get(IndexGeneration, generation_id)
        file = make_file(make_model())
        stored = make_passage_vector(generation, file)
        before = (stored.id, stored.unit_key, stored.vector_blob)
        # A later owner can recover the complete contract from durable JSON and
        # consume native f32 vectors with no model files or embedding calls.
        with Session(db_session.get_bind()) as fresh:
            space = fresh.get(EmbeddingSpace, generation.space_id)
            adopted = SpaceContract(**json.loads(space.config_json))
            neighbors = store.query(
                fresh,
                actor,
                generation_id=generation_id,
                space=adopted,
                vector=struct.unpack("<3f", stored.vector_blob),
            )
        assert [item.subject_id for item in neighbors.items] == [file.model_id]
        db_session.refresh(stored)
        assert (stored.id, stored.unit_key, stored.vector_blob) == before
        assert adopted == provider.space

    def test_refuses_query_against_inactive_space(self, db_session, index):
        provider, generation_id, actor, *_ = index
        with pytest.raises(EmbeddingError, match="embedding_space_mismatch"):
            store.query(
                db_session,
                actor,
                generation_id=generation_id,
                space=replace(provider.space, model_revision="another"),
                vector=[1, 0, 0],
            )


class TestUnitIdentity:
    @pytest.mark.parametrize(
        "file_id,component,digest",
        [
            (0, 0, "a" * 64),
            (2**63, 0, "a" * 64),
            (1, -1, "a" * 64),
            (1, 2049, "a" * 64),
            (1, 0, "bad"),
        ],
    )
    def test_refuses_invalid_unit_identity(self, file_id, component, digest):
        with pytest.raises(EmbeddingError, match="embedding_unit_invalid"):
            store.unit_key(file_id, component, digest, "{}")

    @pytest.mark.parametrize(
        "key", ["invalid", "mesh:1:2049:" + "a" * 64 + ":" + "a" * 16]
    )
    def test_refuses_unknown_component_encoding(self, key):
        assert store.unit_component(key) is None
