"""A consumer can use Spaces and vectors without SimilarityRun or model files."""

import pytest
from printstash_core.inference import EmbeddingSpace as SpaceContract
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import literal
from sqlmodel import select

from app.db.models import PassageVector, SearchPassage
from app.modules.search import vector_store
from app.modules.search.access import visible_passage_ids
from app.modules.search.passages import sync_subject


@pytest.fixture
def document_unit(db_session, make_document, make_user, make_index_generation):
    actor = make_user(superuser=True)
    doc = make_document(name="Bracket guide", body="mount a shelf")
    sync_subject(db_session, SearchSubject(SubjectType.DOCUMENT, doc.id))
    passage = db_session.exec(select(SearchPassage)).one()
    contract = SpaceContract(
        "fixture-text", "v1", 3, "text", "passages-v1", profile="passage"
    )
    space = vector_store.register_space(db_session, contract)
    generation = make_index_generation(space, active_profile_key="text/passage")
    db_session.commit()
    return actor, doc, passage, contract, generation


def source(session, actor, passage_id, input_hash):
    return select(
        SearchPassage.subject_type,
        SearchPassage.subject_id,
        literal(None).label("model_id"),
        literal(None).label("file_id"),
        SearchPassage.id.label("passage_id"),
    ).where(
        SearchPassage.id == passage_id,
        SearchPassage.content_hash == input_hash,
        SearchPassage.id.in_(visible_passage_ids(session, actor)),
    )


class TestVectorStore:
    def test_publishes_a_document_without_similarity(self, db_session, document_unit):
        actor, doc, passage, contract, generation = document_unit
        assert vector_store.publish(
            db_session,
            generation_id=generation.id,
            space=contract,
            unit_kind="text_passage",
            unit_key=f"passage:{passage.id}",
            input_hash=passage.content_hash,
            vector=[3, 4, 0],
            source=source(db_session, actor, passage.id, passage.content_hash),
        )
        db_session.commit()
        result = vector_store.query(
            db_session,
            generation_id=generation.id,
            space=contract,
            vector=[3, 4, 0],
            allowed_ids=select(PassageVector.id).where(
                PassageVector.passage_id.in_(visible_passage_ids(db_session, actor))
            ),
        )
        assert [(row.subject_type, row.subject_id) for row in result.items] == [
            ("document", doc.id)
        ]
        assert result.items[0].score == pytest.approx(1.0)
        stored = db_session.exec(select(PassageVector)).one()
        assert stored.file_id is stored.model_id is None
        assert stored.passage_id == passage.id
        assert len(stored.vector_blob) == 12

    def test_refuses_stale_source_publication(self, db_session, document_unit):
        actor, doc, passage, contract, generation = document_unit
        old_hash = passage.content_hash
        doc.body = "new instructions"
        db_session.add(doc)
        sync_subject(db_session, SearchSubject(SubjectType.DOCUMENT, doc.id))
        db_session.commit()
        assert not vector_store.publish(
            db_session,
            generation_id=generation.id,
            space=contract,
            unit_kind="text_passage",
            unit_key=f"passage:{passage.id}",
            input_hash=old_hash,
            vector=[1, 0, 0],
            source=source(db_session, actor, passage.id, old_hash),
        )
        assert db_session.exec(select(PassageVector)).all() == []

    def test_stages_vectors_without_committing_source(self, db_session, document_unit):
        actor, doc, passage, contract, generation = document_unit
        vector_store.publish(
            db_session,
            generation_id=generation.id,
            space=contract,
            unit_kind="text_passage",
            unit_key=f"passage:{passage.id}",
            input_hash=passage.content_hash,
            vector=[1, 0, 0],
            source=source(db_session, actor, passage.id, passage.content_hash),
        )
        db_session.rollback()
        assert db_session.exec(select(PassageVector)).all() == []

    @pytest.mark.parametrize(
        "field,value",
        [
            ("profile", "wrong_profile"),
            ("provider", "wrong_provider"),
            ("recipe_json", "wrong_recipe"),
            ("model_key", "wrong_model"),
            ("prefixes_json", '{"query":"different","document":""}'),
        ],
    )
    def test_refuses_corrupt_immutable_metadata(
        self, db_session, document_unit, field, value
    ):
        from printstash_core.inference import EmbeddingError

        from app.db.models import EmbeddingSpace

        _, _, _, contract, generation = document_unit
        row = db_session.get(EmbeddingSpace, generation.space_id)
        setattr(row, field, value)
        db_session.add(row)
        db_session.commit()
        with pytest.raises(EmbeddingError, match="embedding_space_corrupt"):
            vector_store.register_space(db_session, contract)

    def test_scopes_an_independent_consumer_to_view_permissions(
        self, db_session, document_unit, make_user
    ):
        _, _, passage, contract, generation = document_unit
        outsider = make_user()
        assert not vector_store.publish(
            db_session,
            generation_id=generation.id,
            space=contract,
            unit_kind="text_passage",
            unit_key=f"passage:{passage.id}",
            input_hash=passage.content_hash,
            vector=[1, 0, 0],
            source=source(db_session, outsider, passage.id, passage.content_hash),
        )
        assert db_session.exec(select(PassageVector)).all() == []

    @pytest.mark.parametrize(
        "changes",
        [
            {"vector": [1, 0]},
            {"unit_kind": "bad-kind"},
            {"unit_key": "x" * 129},
            {"input_hash": "not-a-sha256"},
        ],
    )
    def test_rejects_invalid_consumer_units(self, db_session, document_unit, changes):
        from printstash_core.inference import EmbeddingError

        actor, _, passage, contract, generation = document_unit
        values = dict(
            generation_id=generation.id,
            space=contract,
            unit_kind="text_passage",
            unit_key=f"passage:{passage.id}",
            input_hash=passage.content_hash,
            vector=[1, 0, 0],
            source=source(db_session, actor, passage.id, passage.content_hash),
        )
        with pytest.raises(EmbeddingError):
            vector_store.publish(db_session, **(values | changes))
        assert db_session.exec(select(PassageVector)).all() == []
