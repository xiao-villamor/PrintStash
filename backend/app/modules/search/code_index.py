"""Portable compressed-code derivatives for bounded NumPy shortlist selection."""

from __future__ import annotations

from printstash_core.inference import EmbeddingError
from printstash_core.inference.transforms import IndexTransform, shortlist_codes
from sqlalchemy import Integer, Select, column, table, text
from sqlmodel import Session

from app.db.models import IndexGeneration, PassageVector
from app.db.transactions import begin_write


def table_name(generation_id: int) -> str:
    if type(generation_id) is not int or not 1 <= generation_id < 2**63:
        raise EmbeddingError("embedding_generation_invalid")
    return f"code_gen_{generation_id}"


def prepare(
    session: Session, generation: IndexGeneration, transform: IndexTransform
) -> None:
    begin_write(session)
    name = table_name(generation.id)
    storage_type = (
        "BYTEA" if session.get_bind().dialect.name == "postgresql" else "BLOB"
    )
    session.execute(text(f"DROP TABLE IF EXISTS {name}"))
    session.execute(
        text(
            f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, embedding {storage_type} NOT NULL CHECK (length(embedding)={transform.code_bytes}))"
        )
    )
    generation.vector_table_name = name
    generation.index_state = "building"
    generation.indexed_after_id = 0
    session.add(generation)
    session.flush()


def replace(
    session: Session,
    generation: IndexGeneration,
    transform: IndexTransform,
    row: PassageVector,
) -> None:
    name = table_name(generation.id)
    if generation.vector_table_name != name:
        raise EmbeddingError("embedding_index_unavailable")
    code = transform.encode(row.vector_blob)
    session.execute(
        text(
            f"INSERT INTO {name}(id,embedding) VALUES (:id,:code) ON CONFLICT(id) DO UPDATE SET embedding=excluded.embedding"
        ),
        {"id": row.id, "code": code},
    )


def shortlist(
    session: Session,
    generation: IndexGeneration,
    transform: IndexTransform,
    native_query: bytes,
    allowed_ids: Select,
    *,
    limit: int,
    max_scan: int = 100_000,
) -> tuple[int, ...]:
    name = table_name(generation.id)
    if generation.vector_table_name != name:
        raise EmbeddingError("embedding_index_unavailable")
    codes = table(name, column("id", Integer), column("embedding"))
    statement = (
        codes.select()
        .where(codes.c.id.in_(allowed_ids))
        .order_by(codes.c.id)
        .limit(max_scan + 1)
        .execution_options(yield_per=256)
    )
    rows = session.execute(statement)
    return shortlist_codes(
        transform,
        transform.encode(native_query),
        ((row.id, row.embedding) for row in rows),
        limit=limit,
        max_scan=max_scan,
    ).ids
