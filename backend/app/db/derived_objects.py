"""Exact registry of reconstructible database objects owned by the application."""

from sqlalchemy import Connection, text

SEARCH_FTS = "search_passages_fts"
SEARCH_FTS_OBJECTS = frozenset(
    {
        SEARCH_FTS,
        *(SEARCH_FTS + suffix for suffix in ("_data", "_idx", "_docsize", "_config")),
    }
)


def managed_names(connection: Connection | None) -> frozenset[str]:
    if connection is None:
        return frozenset()
    if connection.dialect.name == "postgresql":
        exists = connection.execute(
            text("SELECT to_regclass('index_generations')")
        ).scalar()
        if exists is None:
            return frozenset()
        names = set()
        # Older revisions do not yet contain a native table mapping.
        mapped = connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='index_generations' AND column_name='vector_table_name'"
            )
        ).first()
        if mapped is None:
            return frozenset()
        for id, name in connection.execute(
            text(
                "SELECT id, vector_table_name FROM index_generations WHERE vector_table_name IS NOT NULL"
            )
        ):
            if name == f"code_gen_{id}" and type(id) is int and id > 0:
                columns = connection.execute(
                    text(
                        "SELECT column_name, udt_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=:name ORDER BY ordinal_position"
                    ),
                    {"name": name},
                ).all()
                if columns == [("id", "int4"), ("embedding", "bytea")]:
                    names.add(name)
            elif name == f"gen_vectors_{id}" and type(id) is int and id > 0:
                vector_column = connection.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=:name AND column_name='embedding' AND udt_name IN ('vector', 'bit')"
                    ),
                    {"name": name},
                ).first()
                if vector_column:
                    names.update((name, name + "_hnsw"))
        return frozenset(names)
    if connection.dialect.name != "sqlite":
        return frozenset()
    names = set()
    ddl = connection.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": SEARCH_FTS},
    ).scalar()
    if (
        isinstance(ddl, str)
        and "using fts5(" in ddl.lower()
        and "content='search_passages'" in ddl.lower()
    ):
        names.update(SEARCH_FTS_OBJECTS)
    columns = connection.execute(text("PRAGMA table_info(index_generations)")).all()
    if any(row[1] == "vector_table_name" for row in columns):
        for id, name in connection.execute(
            text(
                "SELECT id, vector_table_name FROM index_generations WHERE vector_table_name IS NOT NULL"
            )
        ):
            if name == f"code_gen_{id}" and type(id) is int and id > 0:
                columns = connection.execute(text(f"PRAGMA table_info({name})")).all()
                if [(column[1], column[2].upper()) for column in columns] == [
                    ("id", "INTEGER"),
                    ("embedding", "BLOB"),
                ]:
                    names.add(name)
                continue
            if name != f"vec_gen_{id}" or type(id) is not int or id < 1:
                continue
            ddl = connection.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
                {"name": name},
            ).scalar()
            if isinstance(ddl, str) and "using vec0(" in ddl.lower():
                names.update(
                    name + suffix
                    for suffix in (
                        "",
                        "_info",
                        "_chunks",
                        "_rowids",
                        "_vector_chunks00",
                    )
                )
    return frozenset(names)
