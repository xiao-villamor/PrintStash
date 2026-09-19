"""Apply the library's canonical Model predicates before every search ranker."""

from app.core.errors import ErrorKind, OperationError
from app.db.models import Model, PassageVector, SearchPassage
from app.modules.library.model_views.filters import filtered_with_rank
from app.schemas.models import ModelFilters


def validate(user, filters: ModelFilters | None):
    if (
        filters
        and (filters.printer_id is not None or filters.printer_presence)
        and not user.is_superuser
    ):
        raise OperationError("printer_filter_admin_required", kind=ErrorKind.FORBIDDEN)
    if filters and filters.q:
        raise OperationError("search_filter_query_separate", kind=ErrorKind.INVALID)


def model_ids(session, user, filters):
    validate(user, filters)
    return (
        filtered_with_rank(session, user, filters or ModelFilters())[0]
        .with_only_columns(Model.id)
        .correlate(None)
    )


def passages(statement, session, user, filters):
    if filters is None:
        return statement
    return statement.where(
        SearchPassage.subject_type == "model",
        SearchPassage.subject_id.in_(model_ids(session, user, filters)),
    )


def vectors(statement, session, user, filters):
    if filters is None:
        return statement
    return statement.where(
        PassageVector.subject_type == "model",
        PassageVector.subject_id.in_(model_ids(session, user, filters)),
    )
