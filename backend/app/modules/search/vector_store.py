"""Shared durable native vectors; consumers supply fresh authorized source SQL.

This is the single vector store for search and optional consumers. Provider
runtime, source permissions and job ownership remain outside this module.
Mutations stage changes in the caller's transaction, except initialize(), whose
separate validation transaction is explicit in its contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, replace
from typing import Iterable, Protocol

from printstash_core.inference import EmbeddingError
from printstash_core.inference import EmbeddingSpace as SpaceContract
from printstash_core.inference.vectors import (
    NeighborResult,
    VectorEntry,
    cosine_neighbors,
    normalize,
)
from sqlalchemy import Integer, Select, cast, literal
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.time import utcnow
from app.db.models import EmbeddingSpace, IndexGeneration, PassageVector
from app.db.session import SessionFactory
from app.db.transactions import begin_write
from app.modules.search import vector_index


class ValidatedProvider(Protocol):
    @property
    def space(self) -> SpaceContract: ...
    def validate(self) -> object: ...


def register_space(session: Session, contract: SpaceContract) -> EmbeddingSpace:
    """Idempotently persist the complete immutable inference identity."""
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert

    def encode(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    session.exec(
        insert(EmbeddingSpace)
        .values(
            config_hash=contract.config_hash,
            modality=contract.modality,
            profile=contract.profile,
            provider=contract.provider,
            model_key=contract.model_key,
            model_revision=contract.model_revision,
            native_dimension=contract.dimension,
            normalization=contract.normalization,
            prefixes_json=encode(
                {"query": contract.query_prefix, "document": contract.document_prefix}
            ),
            recipe_json=contract.render_recipe,
            config_json=encode(asdict(contract)),
            created_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["config_hash"])
    )
    row = session.exec(
        select(EmbeddingSpace).where(EmbeddingSpace.config_hash == contract.config_hash)
    ).one()
    _validate_stored_space(row, contract)
    return row


def _validate_stored_space(row: EmbeddingSpace, contract: SpaceContract) -> None:
    try:
        consistent = (
            SpaceContract(**json.loads(row.config_json)) == contract
            and json.loads(row.prefixes_json)
            == {"query": contract.query_prefix, "document": contract.document_prefix}
            and row.config_hash == contract.config_hash
            and row.native_dimension == contract.dimension
            and row.modality == contract.modality
            and row.profile == contract.profile
            and row.provider == contract.provider
            and row.model_key == contract.model_key
            and row.model_revision == contract.model_revision
            and row.normalization == contract.normalization
            and row.recipe_json == contract.render_recipe
        )
    except (ValueError, TypeError, EmbeddingError):
        consistent = False
    if not consistent:
        raise EmbeddingError("embedding_space_corrupt")


def active_generation(session: Session, space: SpaceContract) -> IndexGeneration | None:
    return session.exec(
        select(IndexGeneration)
        .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
        .where(
            EmbeddingSpace.config_hash == space.config_hash,
            IndexGeneration.state == "active",
        )
    ).first()


def generation_for(
    session: Session,
    generation_id: int,
    space: SpaceContract,
    *,
    states: tuple[str, ...] = ("active",),
) -> IndexGeneration:
    row = session.exec(
        select(IndexGeneration)
        .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
        .where(
            IndexGeneration.id == generation_id,
            EmbeddingSpace.config_hash == space.config_hash,
            EmbeddingSpace.native_dimension == space.dimension,
            col(IndexGeneration.state).in_(states),
        )
    ).first()
    if row is None:
        raise EmbeddingError("embedding_space_mismatch")
    return row


def initialize(sessions: SessionFactory, provider: ValidatedProvider) -> int:
    """Adopt the consumer's first canary-validated generation; never switch it."""
    provider.validate()
    contract = provider.space
    with sessions.scoped_session() as session:
        space = register_space(session, contract)
        existing = active_generation(session, contract)
        if existing is not None:
            assert existing.id is not None
            return existing.id
        try:
            assert space.id is not None
            generation = IndexGeneration(
                space_id=space.id,
                active_profile_key=f"{contract.modality}/{contract.profile}",
                index_dimension=contract.dimension,
            )
            session.add(generation)
            session.commit()
            session.refresh(generation)
            assert generation.id is not None
            return generation.id
        except IntegrityError as exc:
            session.rollback()
            existing = active_generation(session, contract)
            if existing is not None:
                assert existing.id is not None
                return existing.id
            raise EmbeddingError("embedding_active_generation_conflict") from exc


def has_unit(
    session: Session, generation_id: int, key: str, *, kind: str | None = None
) -> bool:
    statement = select(PassageVector.id).where(
        PassageVector.generation_id == generation_id, PassageVector.unit_key == key
    )
    if kind is not None:
        statement = statement.where(PassageVector.unit_kind == kind)
    return session.exec(statement.limit(1)).first() is not None


def publish(
    session: Session,
    *,
    generation_id: int,
    space: SpaceContract,
    unit_kind: str,
    unit_key: str,
    input_hash: str,
    vector: Iterable[float],
    source: Select,
    truncated: bool = False,
    states: tuple[str, ...] = ("active", "building"),
) -> bool:
    """Upsert from a source-fenced SELECT; never commit or invoke inference.

    ``source`` must return at most one current, authorized unit with columns
    subject_type, subject_id, model_id, file_id, passage_id. Consumers include
    their input hash, source liveness, permissions and durable lease in that SQL.
    The generation state is fenced again in the same INSERT, after inference.
    """
    if (
        re.fullmatch(r"[a-z][a-z0-9_]{0,31}", unit_kind) is None
        or not 1 <= len(unit_key) <= 128
        or re.fullmatch(r"[0-9a-f]{64}", input_hash) is None
    ):
        raise EmbeddingError("embedding_unit_invalid")
    blob = normalize(vector, space.dimension)
    generation = generation_for(session, generation_id, space, states=states)
    current = source.subquery()
    generation_live = (
        select(IndexGeneration.id)
        .where(
            IndexGeneration.id == generation_id, col(IndexGeneration.state).in_(states)
        )
        .exists()
    )
    values = select(
        literal(generation_id),
        literal(unit_kind),
        literal(unit_key),
        current.c.subject_type,
        current.c.subject_id,
        cast(current.c.model_id, Integer),
        cast(current.c.file_id, Integer),
        cast(current.c.passage_id, Integer),
        literal(input_hash),
        literal(space.dimension),
        literal(truncated),
        literal(blob),
        literal(utcnow()),
    ).where(generation_live)
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
    statement = insert(PassageVector).from_select(
        [
            "generation_id",
            "unit_kind",
            "unit_key",
            "subject_type",
            "subject_id",
            "model_id",
            "file_id",
            "passage_id",
            "input_hash",
            "native_dimension",
            "truncated",
            "vector_blob",
            "created_at",
        ],
        values,
    )
    # Python's legacy SQLite transaction mode recognizes INSERT but not WITH
    # ... INSERT. Authorized sources use CTEs: explicitly open the transaction
    # so a caller rollback cannot leave a vector committed on its own.
    begin_write(session)
    connection = session.connection()
    result = connection.execute(
        statement.on_conflict_do_update(
            index_elements=["generation_id", "unit_kind", "unit_key"],
            set_={
                key: getattr(statement.excluded, key)
                for key in (
                    "subject_type",
                    "subject_id",
                    "model_id",
                    "file_id",
                    "passage_id",
                    "input_hash",
                    "native_dimension",
                    "truncated",
                    "vector_blob",
                    "created_at",
                )
            },
            where=PassageVector.input_hash != statement.excluded.input_hash,
        ).returning(PassageVector.id)
    )
    row = result.first()
    if row is None:
        return False
    current_vector = session.get(PassageVector, row[0], populate_existing=True)
    assert current_vector is not None
    vector_index.replace(session, generation, current_vector)
    return True


def query(
    session: Session,
    *,
    generation_id: int,
    space: SpaceContract,
    vector: Iterable[float],
    allowed_ids: Select,
    limit: int = 20,
    max_scan: int = 100_000,
    states: tuple[str, ...] = ("active",),
) -> NeighborResult:
    """Bounded full-float neighbors over a caller's fresh authorized SQL set."""
    generation = generation_for(session, generation_id, space, states=states)
    if not 1 <= max_scan <= 1_000_000 or not 1 <= limit <= 100:
        raise EmbeddingError("embedding_query_budget_invalid")
    authorized = allowed_ids.subquery()
    eligible = (
        select(literal(1))
        .select_from(authorized)
        .where(authorized.c[0] == PassageVector.id)
        .correlate(PassageVector)
        .exists()
    )
    statement = select(
        PassageVector.id,
        PassageVector.subject_type,
        PassageVector.subject_id,
        PassageVector.vector_blob,
    ).where(
        PassageVector.generation_id == generation_id,
        PassageVector.native_dimension == space.dimension,
        eligible,
    )
    query_blob = normalize(vector, space.dimension)
    scoped_ids = statement.with_only_columns(PassageVector.id).order_by(
        PassageVector.id
    )
    candidates = vector_index.shortlist(
        session,
        generation,
        query_blob,
        scoped_ids.limit(max_scan),
        limit=min(2048, limit * 8),
        max_scan=max_scan,
    )
    if candidates is not None:
        rows = session.exec(
            statement.where(col(PassageVector.id).in_(candidates))
        ).all()
        result = cosine_neighbors(
            query_blob,
            (
                VectorEntry(id, subject_id, blob, kind)
                for id, kind, subject_id, blob in rows
            ),
            dimension=space.dimension,
            limit=limit,
            max_scan=max_scan,
        )
        capped = len(candidates) == min(2048, limit * 8) or (
            session.exec(scoped_ids.offset(max_scan).limit(1)).first() is not None
        )
        return replace(
            result,
            backend=vector_index.serving_backend(generation),
            truncated=capped,
        )
    rows = session.exec(
        statement.order_by(PassageVector.id)
        .limit(max_scan + 1)
        .execution_options(yield_per=256)
    )
    return cosine_neighbors(
        query_blob,
        (
            VectorEntry(unit_id, subject_id, blob, kind)
            for unit_id, kind, subject_id, blob in rows
        ),
        dimension=space.dimension,
        limit=limit,
        max_scan=max_scan,
    )
