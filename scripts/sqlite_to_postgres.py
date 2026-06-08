#!/usr/bin/env python3
"""Copy all tables from a SQLite database into a Postgres database.

Usage:
    python scripts/sqlite_to_postgres.py \
        --sqlite sqlite:////data/db/printstash.sqlite \
        --postgres postgresql://user:pass@localhost:5432/printstash
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable

from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.engine import Engine


def _ordered_table_names(table_names: Iterable[str]) -> list[str]:
    preferred = [
        "collections",
        "models",
        "files",
        "metadata",
        "tags",
        "model_tags",
        "users",
        "system_config",
        "printers",
        "print_jobs",
        "refresh_tokens",
    ]
    names = list(table_names)
    known = [t for t in preferred if t in names]
    remaining = sorted([t for t in names if t not in preferred])
    return known + remaining


def _copy_table(
    source: Engine,
    target: Engine,
    source_table: Table,
    target_table: Table,
) -> int:
    rows = []
    with source.connect() as conn:
        for row in conn.execute(select(source_table)):
            rows.append(dict(row._mapping))
    if not rows:
        return 0
    with target.begin() as conn:
        conn.execute(target_table.insert(), rows)
    return len(rows)


def copy_sqlite_to_postgres(sqlite_url: str, postgres_url: str) -> None:
    source_engine = create_engine(sqlite_url)
    target_engine = create_engine(postgres_url)

    source_meta = MetaData()
    source_meta.reflect(bind=source_engine)
    target_meta = MetaData()
    target_meta.reflect(bind=target_engine)

    table_names = _ordered_table_names(source_meta.tables.keys())

    with target_engine.begin() as conn:
        replication_role_enabled = False
        try:
            conn.execute(text("SET session_replication_role = replica;"))
            replication_role_enabled = True
        except Exception:
            # Works only with elevated Postgres privileges; continue without it.
            pass
        for table_name in reversed(table_names):
            if table_name in target_meta.tables:
                conn.execute(target_meta.tables[table_name].delete())
        if replication_role_enabled:
            conn.execute(text("SET session_replication_role = DEFAULT;"))

    for table_name in table_names:
        if table_name not in source_meta.tables or table_name not in target_meta.tables:
            continue
        copied = _copy_table(
            source_engine,
            target_engine,
            source_meta.tables[table_name],
            target_meta.tables[table_name],
        )
        print(f"{table_name}: {copied} rows copied")

    print("done")


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy SQLite data into Postgres")
    parser.add_argument("--sqlite", required=True, help="SQLite SQLAlchemy URL")
    parser.add_argument("--postgres", required=True, help="Postgres SQLAlchemy URL")
    args = parser.parse_args()
    copy_sqlite_to_postgres(args.sqlite, args.postgres)


if __name__ == "__main__":
    main()
