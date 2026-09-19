"""Small semantic candidate sets retain bounded fresh permission checks."""

import json

from printstash_core.inference import EmbeddingSpace
from printstash_core.search.passages import SearchSubject, SubjectType

from app.db.models import PassageVector
from app.modules.search.query_context import SemanticLeg, allowed_vectors
from app.modules.search.text_inputs import TextRecipe
from tests.fakes.sqlite_work import sqlite_work


class TestAllowedVectors:
    def test_bounds_reauthorization_to_candidate_identities(
        self,
        db_session,
        make_user,
        make_model,
        make_search_passage,
        make_embedding_space,
        make_index_generation,
        make_passage_vector,
    ):
        actor = make_user(superuser=True)
        target = make_model("Target")
        passage = make_search_passage(SearchSubject(SubjectType.MODEL, target.id))
        stored = make_embedding_space(
            modality="text", profile="text", recipe_json=TextRecipe().encode()
        )
        generation = make_index_generation(stored)
        vector = make_passage_vector(generation, passage=passage)
        leg = SemanticLeg(
            "semantic_text",
            generation.id,
            EmbeddingSpace(**json.loads(stored.config_json)),
            0.1,
            1,
            1,
        )
        db_session.commit()
        statement = allowed_vectors(db_session, actor, leg, (SubjectType.MODEL,)).where(
            PassageVector.id == vector.id
        )
        with sqlite_work(db_session) as small:
            before = db_session.exec(statement).all()
        for index in range(1000):
            model = make_model(f"Unrelated {index}")
            make_search_passage(SearchSubject(SubjectType.MODEL, model.id))
        db_session.commit()
        statement = allowed_vectors(db_session, actor, leg, (SubjectType.MODEL,)).where(
            PassageVector.id == vector.id
        )
        with sqlite_work(db_session) as large:
            after = db_session.exec(statement).all()
        assert before == after == [vector.id]
        assert large.instructions <= max(1000, small.instructions * 2)
