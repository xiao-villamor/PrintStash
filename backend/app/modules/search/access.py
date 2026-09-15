"""SQL visibility for Subjects and every contributor before retrieval is scored."""

from sqlalchemy import Integer, and_, cast, false, func, literal, select, union_all
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Session

from app.db.models import (
    SENTINEL_MODEL_HASH,
    Collection,
    Document,
    Model,
    MultipartModel,
    SearchPassage,
    User,
)
from app.db.scopes import live
from app.modules.identity.rbac import accessible_collection_ids
from app.modules.library.model_views.access import accessible_live_model_ids_stmt


def visible_subjects(session: Session, user: User):
    collections = accessible_collection_ids(session, user) if user.is_active else set()
    models = accessible_live_model_ids_stmt(session, user)
    if not user.is_active:
        models = models.where(false())
    statements = [
        models.with_only_columns(
            literal("model").label("kind"), *models.selected_columns
        )
    ]
    for kind, table in (
        ("collection", Collection),
        ("document", Document),
        ("multipart_model", MultipartModel),
    ):
        statement = select(literal(kind).label("kind"), table.id)
        if table is not MultipartModel:
            statement = statement.where(live(table))
        if not user.is_active:
            statement = statement.where(false())
        elif not user.is_superuser:
            owner_collection = table.id if table is Collection else table.collection_id
            statement = statement.where(owner_collection.in_(collections))
        statements.append(statement)
    return union_all(*statements).cte()


def visible_passage_ids(session: Session, user: User):
    return select(SearchPassage.id).where(visible_passage_clause(session, user))


def visible_passage_clause(session: Session, user: User):
    """Correlate visibility to the caller's passage rows, including small result sets."""
    return _passage_visibility(session, visible_subjects(session, user))


def indexable_passage_ids(session: Session):
    """Instance-consented background indexing includes only live contributors.

    This is not a user authorization scope. Interactive retrieval must continue
    to use visible_passage_ids with its freshly authenticated principal.
    """
    statements = []
    for kind, table in (
        ("model", Model),
        ("collection", Collection),
        ("document", Document),
        ("multipart_model", MultipartModel),
    ):
        statement = select(literal(kind).label("kind"), table.id)
        if table is not MultipartModel:
            statement = statement.where(live(table))
        if table is Model:
            statement = statement.where(Model.hash != SENTINEL_MODEL_HASH)
        statements.append(statement)
    return select(SearchPassage.id).where(
        _passage_visibility(session, union_all(*statements).cte())
    )


def _passage_visibility(session: Session, visible):
    owner_visible = (
        select(visible.c.id)
        .where(
            visible.c.kind == SearchPassage.subject_type,
            visible.c.id == SearchPassage.subject_id,
        )
        .exists()
    )
    if session.get_bind().dialect.name == "postgresql":
        dependencies = func.jsonb_array_elements(
            cast(SearchPassage.access_dependencies_json, JSONB)
        ).table_valued("value")
        kind = dependencies.c.value.op("->>")(0)
        id = cast(dependencies.c.value.op("->>")(1), Integer)
    else:
        dependencies = func.json_each(
            SearchPassage.access_dependencies_json
        ).table_valued("value")
        kind = func.json_extract(dependencies.c.value, "$[0]")
        id = func.json_extract(dependencies.c.value, "$[1]")
    dependency_visible = (
        select(visible.c.id)
        .where(visible.c.kind == kind, visible.c.id == id)
        .correlate(dependencies)
        .exists()
    )
    hidden_dependency = (
        select(literal(1))
        .select_from(dependencies)
        .where(~dependency_visible)
        .correlate(SearchPassage)
        .exists()
    )
    return and_(owner_visible, ~hidden_dependency)


def passage_in_scope(allowed_ids):
    """Preserve an arbitrary ID set while allowing indexed candidate lookups.

    An IN subquery can enumerate every visible passage even when the outer
    ranker or response has only a few candidates. Correlation keeps any LIMIT,
    DISTINCT or join inside the supplied scope and permits predicate pushdown.
    """
    scope = allowed_ids.subquery()
    return (
        select(literal(1))
        .select_from(scope)
        .where(scope.c[0] == SearchPassage.id)
        .correlate(SearchPassage)
        .exists()
    )
