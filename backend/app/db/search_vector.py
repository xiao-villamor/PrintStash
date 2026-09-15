"""A managed PostgreSQL generated tsvector with an inert SQLite counterpart."""

from sqlalchemy import Text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement


class SearchVectorExpression(FunctionElement):
    type = Text()
    inherit_cache = True


@compiles(SearchVectorExpression)
def _portable_vector(_element, _compiler, **_kwargs):
    return "''"


@compiles(SearchVectorExpression, "postgresql")
def _postgres_vector(_element, _compiler, **_kwargs):
    return "to_tsvector('simple'::regconfig, title || ' ' || tags_text || ' ' || text)"
