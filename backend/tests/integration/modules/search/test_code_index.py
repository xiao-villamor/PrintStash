"""Compressed derivative tables retain full native vectors and filter before scoring."""

import json
import struct

import pytest
from printstash_core.inference import EmbeddingSpace as SpaceContract
from printstash_core.inference.transforms import IndexTransform
from printstash_core.inference.vectors import normalize
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import text
from sqlmodel import select

from app.db.derived_objects import managed_names
from app.db.models import PassageVector
from app.modules.search import code_index, vector_index, vector_store


@pytest.fixture(params=["float32", "int8", "binary"], ids=["float", "int8", "binary"])
def compressed_generation(
    request,
    db_session,
    make_embedding_space,
    make_index_generation,
    make_document,
    make_search_passage,
    make_passage_vector,
):
    transform = IndexTransform.approved(8, 8, request.param)
    generation = make_index_generation(
        make_embedding_space(native_dimension=8),
        transform_json=transform.metadata(),
        quantization=request.param,
    )
    first_passage = make_search_passage(
        SearchSubject(SubjectType.DOCUMENT, make_document("Assembly lid").id)
    )
    second_passage = make_search_passage(
        SearchSubject(SubjectType.DOCUMENT, make_document("Assembly base").id)
    )
    first = make_passage_vector(generation, passage=first_passage)
    second = make_passage_vector(
        generation,
        passage=second_passage,
        vector_blob=struct.pack("<8f", 0, 1, 0, 0, 0, 0, 0, 0),
    )
    code_index.prepare(db_session, generation, transform)
    code_index.replace(db_session, generation, transform, first)
    code_index.replace(db_session, generation, transform, second)
    db_session.commit()
    try:
        yield generation, transform, first, second
    finally:
        db_session.rollback()
        db_session.execute(
            text(f"DROP TABLE IF EXISTS {code_index.table_name(generation.id)}")
        )
        db_session.commit()


class TestPrepare:
    def test_identifies_only_registered_compressed_tables(
        self, db_session, compressed_generation
    ):
        generation, *_ = compressed_generation
        db_session.execute(
            text("CREATE TABLE code_gen_999999(id INTEGER PRIMARY KEY, embedding BLOB)")
        )
        try:
            names = managed_names(db_session.connection())
            assert code_index.table_name(generation.id) in names
            assert "code_gen_999999" not in names
        finally:
            db_session.execute(text("DROP TABLE code_gen_999999"))

    def test_stores_compact_derivatives(self, db_session, compressed_generation):
        generation, transform, first, second = compressed_generation

        sizes = (
            db_session.execute(
                text(
                    f"SELECT length(embedding) FROM {code_index.table_name(generation.id)} ORDER BY id"
                )
            )
            .scalars()
            .all()
        )

        assert sizes == [transform.code_bytes, transform.code_bytes]
        assert [len(row.vector_blob) for row in (first, second)] == [32, 32]

    def test_rebuilds_portable_codes_from_native_vectors(
        self, db_session, compressed_generation
    ):
        generation, transform, first, _ = compressed_generation
        db_session.execute(text(f"DROP TABLE {generation.vector_table_name}"))
        generation.index_state = "absent"
        if transform.quantization == "float32":
            # Full floats on NumPy need no extra derivative table.
            assert not vector_index.prepare(db_session, generation)
        else:
            assert vector_index.rebuild_partition(db_session, generation) == 2
            assert generation.index_state == "ready"
            assert code_index.shortlist(
                db_session,
                generation,
                transform,
                first.vector_blob,
                select(PassageVector.id),
                limit=1,
            ) == (first.id,)


@pytest.fixture(params=["int8", "binary"])
def ranking_corpus(
    request,
    db_session,
    make_embedding_space,
    make_index_generation,
    make_document,
    make_search_passage,
    make_passage_vector,
):
    import numpy as np

    random = np.random.default_rng(166)
    vectors = random.normal(size=(256, 64))
    vectors /= np.linalg.norm(vectors, axis=1)[:, None]
    queries = random.normal(size=(16, 64))
    queries /= np.linalg.norm(queries, axis=1)[:, None]
    space = make_embedding_space(native_dimension=64)
    transform = IndexTransform(64, 64, request.param)
    generation = make_index_generation(
        space, quantization=request.param, transform_json=transform.metadata()
    )
    ids = []
    for vector in vectors:
        passage = make_search_passage(
            SearchSubject(SubjectType.DOCUMENT, make_document().id)
        )
        row = make_passage_vector(
            generation, passage=passage, vector_blob=normalize(vector, 64)
        )
        ids.append(row.id)
    assert vector_index.prepare(db_session, generation)
    while vector_index.rebuild_partition(db_session, generation):
        pass
    db_session.commit()
    try:
        yield (
            generation,
            SpaceContract(**json.loads(space.config_json)),
            transform,
            vectors,
            queries,
            ids,
        )
    finally:
        db_session.rollback()
        generation.state = "retired"
        vector_index.drop(db_session, generation)
        db_session.commit()


class TestRankingRecall:
    def test_measures_compressed_ranking_recall(self, db_session, ranking_corpus):
        import numpy as np

        generation, space, transform, vectors, queries, ids = ranking_corpus
        recalls = []
        for query in queries:
            expected = {ids[index] for index in np.argsort(-(vectors @ query))[:10]}
            result = vector_store.query(
                db_session,
                generation_id=generation.id,
                space=space,
                vector=query,
                allowed_ids=select(PassageVector.id),
                limit=10,
            )
            recalls.append(
                len(expected.intersection(item.unit_id for item in result.items)) / 10
            )
            for item in result.items:
                assert item.score == pytest.approx(
                    float(vectors[ids.index(item.unit_id)] @ query), abs=1e-6
                )
        recall = sum(recalls) / len(recalls)
        code_bytes = db_session.execute(
            text(f"SELECT sum(length(embedding)) FROM {generation.vector_table_name}")
        ).scalar_one()
        native_bytes = sum(
            len(blob)
            for blob in db_session.exec(
                select(PassageVector.vector_blob).where(
                    PassageVector.generation_id == generation.id
                )
            )
        )
        print(
            f"{transform.quantization}: recall@10={recall:.4f}; derived={code_bytes}; native={native_bytes}; total_vector_payload={code_bytes + native_bytes}"
        )
        assert recall >= (0.95 if transform.quantization == "int8" else 0.8)
        assert code_bytes == 256 * transform.code_bytes
        assert native_bytes == 256 * 64 * 4


class TestShortlist:
    def test_rejects_foreign_compressed_table_claims(
        self, db_session, compressed_generation
    ):
        from printstash_core.inference import EmbeddingError

        generation, transform, first, _ = compressed_generation
        original = generation.vector_table_name
        generation.vector_table_name = "code_gen_999999"

        with pytest.raises(EmbeddingError, match="embedding_index_unavailable"):
            code_index.replace(db_session, generation, transform, first)
        with pytest.raises(EmbeddingError, match="embedding_index_unavailable"):
            code_index.shortlist(
                db_session,
                generation,
                transform,
                first.vector_blob,
                select(PassageVector.id),
                limit=1,
            )

        generation.vector_table_name = original
        assert code_index.shortlist(
            db_session,
            generation,
            transform,
            first.vector_blob,
            select(PassageVector.id),
            limit=1,
        ) == (first.id,)

    def test_filters_before_compressed_scoring(self, db_session, compressed_generation):
        generation, transform, first, second = compressed_generation

        candidates = code_index.shortlist(
            db_session,
            generation,
            transform,
            first.vector_blob,
            select(PassageVector.id).where(PassageVector.id == second.id),
            limit=1,
        )

        assert candidates == (second.id,)

    def test_finds_the_matching_native_vector(self, db_session, compressed_generation):
        generation, transform, first, _ = compressed_generation

        candidates = code_index.shortlist(
            db_session,
            generation,
            transform,
            first.vector_blob,
            select(PassageVector.id),
            limit=1,
        )

        assert candidates == (first.id,)
