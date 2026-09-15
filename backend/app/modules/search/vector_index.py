"""Reconstructible native indexes; full native float32 remains authoritative."""

from __future__ import annotations

import struct

from printstash_core.inference import EmbeddingError
from printstash_core.inference.transforms import IndexTransform
from sqlalchemy import Integer, Select, and_, column, func, literal, or_, table, text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import (
    EmbeddingSpace,
    IndexGeneration,
    PassageVector,
    SearchReconciliationState,
)
from app.db.transactions import begin_write
from app.db.vector_extensions import load_sqlite_vector_extension
from app.modules.search import code_index


def transform_for(session: Session, generation: IndexGeneration) -> IndexTransform:
    space = session.get(EmbeddingSpace, generation.space_id)
    if space is None:
        raise EmbeddingError("embedding_space_unavailable")
    return IndexTransform.restore(
        generation.transform_json,
        native_dimension=space.native_dimension,
        index_dimension=generation.index_dimension,
        quantization=generation.quantization,
    )


def portable(generation: IndexGeneration, transform: IndexTransform) -> bool:
    return (
        generation.index_backend == "numpy"
        and (
            transform.quantization != "float32"
            or transform.index_dimension != transform.native_dimension
        )
    ) or (generation.index_backend == "pgvector" and transform.quantization == "int8")


def serving_backend(generation: IndexGeneration) -> str:
    if (
        generation.index_state != "ready"
        or not settings.search_native_vectors_enabled
        or generation.index_backend == "numpy"
        or (
            generation.index_backend == "pgvector" and generation.quantization == "int8"
        )
    ):
        return "numpy"
    return generation.index_backend


def _sqlite_value(quantization: str, placeholder: str = ":v") -> str:
    return {
        "float32": placeholder,
        "int8": f"vec_int8({placeholder})",
        "binary": f"vec_bit({placeholder})",
    }[quantization]


def _postgres_value(transform: IndexTransform, code: bytes) -> str:
    if transform.quantization == "binary":
        return "".join(f"{byte:08b}"[::-1] for byte in code)
    values = struct.unpack(f"<{transform.index_dimension}f", code)
    return "[" + ",".join(str(value) for value in values) + "]"


def table_name(generation_id: int, dialect: str) -> str:
    if type(generation_id) is not int or not 1 <= generation_id < 2**63:
        raise EmbeddingError("embedding_generation_invalid")
    return ("vec_gen_" if dialect == "sqlite" else "gen_vectors_") + str(generation_id)


def _fallback(session: Session, generation: IndexGeneration, code: str) -> None:
    generation.index_state = "unavailable"
    generation.index_error = code
    session.add(generation)
    session.flush()


def _native_supported(session: Session, generation: IndexGeneration) -> bool:
    if not settings.search_native_vectors_enabled:
        _fallback(session, generation, "embedding_native_disabled")
        return False
    if session.get_bind().dialect.name == "sqlite":
        # Also covers a connection opened before an operator enables the flag.
        if not load_sqlite_vector_extension(
            session.connection().connection.driver_connection
        ):
            _fallback(session, generation, "embedding_sqlite_vec_unavailable")
            return False
    elif generation.index_dimension > 2000 and generation.quantization != "binary":
        _fallback(session, generation, "embedding_pgvector_dimension_unsupported")
        return False
    return True


def prepare(session: Session, generation: IndexGeneration) -> bool:
    """Probe actual native DDL/types in a savepoint; never fail content startup."""
    begin_write(session)
    transform = transform_for(session, generation)
    if portable(generation, transform):
        try:
            with session.begin_nested():
                code_index.prepare(session, generation, transform)
                generation.index_error = None
            return True
        except DBAPIError:
            _fallback(session, generation, "embedding_portable_index_unavailable")
            return False
    if generation.index_backend == "numpy":
        generation.index_state = "ready"
        generation.index_error = None
        session.add(generation)
        session.flush()
        return False
    if not _native_supported(session, generation):
        return False
    name = table_name(generation.id, session.get_bind().dialect.name)
    dimension = transform.storage_dimension
    if type(dimension) is not int or not 1 <= dimension <= 4096:
        raise EmbeddingError("embedding_dimension_invalid")
    try:
        with session.begin_nested():
            session.execute(text(f"DROP TABLE IF EXISTS {name}"))
            if session.get_bind().dialect.name == "sqlite":
                kind = {"float32": "float", "int8": "int8", "binary": "bit"}[
                    transform.quantization
                ]
                metric = (
                    ""
                    if transform.quantization == "binary"
                    else " distance_metric=cosine"
                )
                session.execute(
                    text(
                        f"CREATE VIRTUAL TABLE {name} USING vec0(embedding {kind}[{dimension}]{metric})"
                    )
                )
                probe = transform.encode(
                    struct.pack(
                        f"<{transform.native_dimension}f",
                        1,
                        *([0] * (transform.native_dimension - 1)),
                    )
                )
                session.execute(
                    text(
                        f"INSERT INTO {name}(rowid,embedding) VALUES (0,{_sqlite_value(transform.quantization)})"
                    ),
                    {"v": probe},
                )
                assert (
                    session.execute(
                        text(f"SELECT vec_length(embedding) FROM {name} WHERE rowid=0")
                    ).scalar_one()
                    == dimension
                )
                session.execute(text(f"DELETE FROM {name} WHERE rowid=0"))
            else:
                # Availability alone is insufficient: permission or extension
                # installation can fail. Only this explicit prepare attempts it.
                session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                kind = "bit" if transform.quantization == "binary" else "vector"
                opclass = "bit_hamming_ops" if kind == "bit" else "vector_cosine_ops"
                session.execute(
                    text(
                        f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, embedding {kind}({dimension}) NOT NULL)"
                    )
                )
                session.execute(
                    text(
                        f"CREATE INDEX {name}_hnsw ON {name} USING hnsw (embedding {opclass})"
                    )
                )
                probe = (
                    "1" + "0" * (dimension - 1)
                    if kind == "bit"
                    else "[1" + ",0" * (dimension - 1) + "]"
                )
                length = "bit_length" if kind == "bit" else "vector_dims"
                actual = session.execute(
                    text(f"SELECT {length}(CAST(:v AS {kind}({dimension})))"),
                    {"v": probe},
                ).scalar_one()
                if actual != dimension:
                    raise EmbeddingError("embedding_dimension_mismatch")
            generation.vector_table_name = name
            generation.index_state = "building"
            generation.index_error = None
            generation.indexed_after_id = 0
            session.add(generation)
            session.flush()
        return True
    except (DBAPIError, EmbeddingError):
        _fallback(session, generation, "embedding_native_unavailable")
        return False


def replace(session: Session, generation: IndexGeneration, row: PassageVector) -> None:
    """Update one derived unit transactionally; adapter errors preserve floats."""
    if generation.index_state not in {"building", "ready"}:
        return
    transform = transform_for(session, generation)
    if portable(generation, transform):
        try:
            with session.begin_nested():
                code_index.replace(session, generation, transform, row)
        except (DBAPIError, EmbeddingError):
            _fallback(session, generation, "embedding_portable_index_unavailable")
        return
    if generation.index_backend == "numpy":
        return
    name = table_name(generation.id, session.get_bind().dialect.name)
    if generation.vector_table_name != name:
        _fallback(session, generation, "embedding_native_unavailable")
        return
    try:
        with session.begin_nested():
            code = transform.encode(row.vector_blob)
            if session.get_bind().dialect.name == "sqlite":
                session.execute(
                    text(f"DELETE FROM {name} WHERE rowid=:id"), {"id": row.id}
                )
                session.execute(
                    text(
                        f"INSERT INTO {name}(rowid,embedding) VALUES (:id,{_sqlite_value(transform.quantization)})"
                    ),
                    {"id": row.id, "v": code},
                )
            else:
                encoded = _postgres_value(transform, code)
                kind = "bit" if transform.quantization == "binary" else "vector"
                session.execute(
                    text(
                        f"INSERT INTO {name}(id,embedding) VALUES (:id,CAST(:v AS {kind}({transform.storage_dimension}))) ON CONFLICT (id) DO UPDATE SET embedding=EXCLUDED.embedding"
                    ),
                    {"id": row.id, "v": encoded},
                )
    except (DBAPIError, struct.error, EmbeddingError):
        _fallback(session, generation, "embedding_native_unavailable")


def remove(session: Session, generation: IndexGeneration, vector_id: int) -> None:
    if generation.index_state not in {
        "building",
        "ready",
    }:
        return
    name = generation.vector_table_name
    if name not in {
        table_name(generation.id, session.get_bind().dialect.name),
        code_index.table_name(generation.id),
    }:
        # Content writers must not acquire generation row locks after passage
        # locks: cutover/publish acquire those in the opposite order. The repair
        # worker probes and reports native availability independently.
        return
    column = (
        "rowid"
        if session.get_bind().dialect.name == "sqlite"
        and name != code_index.table_name(generation.id)
        else "id"
    )
    try:
        with session.begin_nested():
            session.execute(
                text(f"DELETE FROM {name} WHERE {column}=:id"), {"id": vector_id}
            )
    except DBAPIError:
        # Durable deletion and authorized ID filtering already exclude this
        # stale derivative. Never turn native cleanup into a content failure.
        return


def rebuild_partition(
    session: Session, generation: IndexGeneration, *, limit: int = 128
) -> int:
    if not 1 <= limit <= 1024:
        raise EmbeddingError("embedding_rebuild_budget_invalid")
    if generation.index_state not in {"building", "ready"} and not prepare(
        session, generation
    ):
        return 0
    if generation.index_state == "ready":
        return 0
    rows = session.exec(
        select(PassageVector)
        .where(
            PassageVector.generation_id == generation.id,
            PassageVector.id > generation.indexed_after_id,
        )
        .order_by(PassageVector.id)
        .limit(limit)
    ).all()
    for row in rows:
        replace(session, generation, row)
        if generation.index_state == "unavailable":
            return 0
    if rows:
        generation.indexed_after_id = rows[-1].id
    if len(rows) < limit:
        generation.index_state = "ready"
    session.add(generation)
    session.flush()
    return len(rows)


def shortlist(
    session: Session,
    generation: IndexGeneration,
    query: bytes,
    allowed_ids: Select,
    *,
    limit: int,
    max_scan: int = 100_000,
) -> tuple[int, ...] | None:
    """Authorized native candidates, or None to request bounded NumPy fallback.

    SQLite vec0 applies its rowid IN filter before KNN. PostgreSQL materializes
    the authorized relation before distance ordering; this deliberately trades
    HNSW's unfiltered speed for permission isolation under restrictive access.
    """
    transform = transform_for(session, generation)
    if portable(generation, transform):
        if generation.index_state != "ready":
            return None
        try:
            with session.begin_nested():
                return code_index.shortlist(
                    session,
                    generation,
                    transform,
                    query,
                    allowed_ids,
                    limit=limit,
                    max_scan=max_scan,
                )
        except (DBAPIError, EmbeddingError):
            return None
    if (
        not settings.search_native_vectors_enabled
        or generation.index_backend == "numpy"
        or generation.index_state != "ready"
    ):
        return None
    if not 1 <= limit <= 2048:
        raise EmbeddingError("embedding_query_budget_invalid")
    dialect = session.get_bind().dialect.name
    name = table_name(generation.id, dialect)
    if generation.vector_table_name != name:
        return None
    try:
        with session.begin_nested():
            code = transform.encode(query)
            if dialect == "sqlite":
                native = table(
                    name,
                    column("rowid", Integer),
                    column("embedding"),
                    column("k", Integer),
                )
                statement = select(native.c.rowid).where(
                    native.c.embedding.op("MATCH")(
                        code
                        if transform.quantization == "float32"
                        else getattr(
                            func,
                            "vec_int8"
                            if transform.quantization == "int8"
                            else "vec_bit",
                        )(code)
                    ),
                    native.c.k == limit,
                    native.c.rowid.in_(allowed_ids),
                )
            else:
                native = table(name, column("id", Integer), column("embedding"))
                authorized = (
                    select(native.c.id, native.c.embedding)
                    .where(native.c.id.in_(allowed_ids))
                    .cte()
                    .prefix_with("MATERIALIZED")
                )
                encoded = _postgres_value(transform, code)
                distance = authorized.c.embedding.op(
                    "<~>" if transform.quantization == "binary" else "<=>"
                )(
                    literal(encoded).cast(
                        _vector_type(
                            f"bit({transform.storage_dimension})"
                            if transform.quantization == "binary"
                            else "vector"
                        )
                    )
                )
                statement = (
                    select(authorized.c.id)
                    .order_by(distance, authorized.c.id)
                    .limit(limit)
                )
            return tuple(session.execute(statement).scalars())
    except (DBAPIError, struct.error, EmbeddingError):
        return None


def _vector_type(kind: str = "vector"):
    from sqlalchemy.types import UserDefinedType

    class VectorType(UserDefinedType):
        cache_ok = True

        def get_col_spec(self, **kw):
            return kind

    return VectorType()


def drop(session: Session, generation: IndexGeneration) -> None:
    """Only a drained retired generation can discard its reconstructible DDL."""
    if generation.state not in {"retired", "cancelled", "failed"}:
        raise EmbeddingError("embedding_generation_not_retired")
    name = generation.vector_table_name
    if name in {
        table_name(generation.id, session.get_bind().dialect.name),
        code_index.table_name(generation.id),
    }:
        session.execute(text(f"DROP TABLE IF EXISTS {name}"))
    generation.vector_table_name = None
    generation.index_state = "absent"
    session.add(generation)
    session.flush()


def repair_partition(session: Session) -> int:
    """Round-robin one live Generation; startup never waits for a full rebuild."""
    checkpoint = session.exec(
        select(SearchReconciliationState).where(
            SearchReconciliationState.subject_type == "vector_index"
        )
    ).first()
    if checkpoint is None:
        checkpoint = SearchReconciliationState(subject_type="vector_index")
        session.add(checkpoint)
        session.flush()
    compressed = or_(
        IndexGeneration.quantization != "float32",
        IndexGeneration.index_dimension != EmbeddingSpace.native_dimension,
    )
    portable_index = and_(
        or_(
            IndexGeneration.index_backend == "numpy",
            and_(
                IndexGeneration.index_backend == "pgvector",
                IndexGeneration.quantization == "int8",
            ),
        ),
        compressed,
    )
    eligible = (
        select(IndexGeneration)
        .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
        .where(
            IndexGeneration.state.in_(("active", "building", "ready")),
            or_(
                portable_index,
                and_(
                    settings.search_native_vectors_enabled,
                    IndexGeneration.index_backend != "numpy",
                ),
            ),
        )
    )
    generation = session.exec(
        eligible.where(IndexGeneration.id > checkpoint.partition_after_id)
        .order_by(IndexGeneration.id)
        .limit(1)
    ).first()
    if generation is None:
        checkpoint.partition_after_id = 0
        session.add(checkpoint)
        session.flush()
        return 0
    checkpoint.partition_after_id = generation.id
    session.add(checkpoint)
    if generation.index_state == "ready":
        transform = transform_for(session, generation)
        name = (
            code_index.table_name(generation.id)
            if portable(generation, transform)
            else table_name(generation.id, session.get_bind().dialect.name)
        )
        try:
            with session.begin_nested():
                session.execute(text(f"SELECT embedding FROM {name} LIMIT 0"))
        except DBAPIError:
            generation.index_state = "absent"
            session.add(generation)
    return rebuild_partition(session, generation)
