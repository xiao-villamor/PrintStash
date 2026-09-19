"""Run with uv run python -m scripts.migrate_database; defaults to dry-run."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import create_engine

from app.db.url import normalize_database_url
from app.modules.administration.database_transfer import DatabaseTransferError, transfer


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify/copy current SQLite data to an empty PostgreSQL database."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--target-env",
        default="PRINTSTASH_TARGET_DB_URL",
        help="Environment variable containing target URL; credentials never appear in argv.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy after validation; default only reports counts and hashes.",
    )
    args = parser.parse_args()
    if not args.source.is_file() or not os.environ.get(args.target_env):
        parser.error("source file and target environment variable are required")
    source = target = None
    try:
        source = create_engine(f"sqlite:///{args.source.resolve()}", hide_parameters=True)
        target = create_engine(normalize_database_url(os.environ[args.target_env]), hide_parameters=True)
        report = transfer(source, target, dry_run=not args.apply)
        print(json.dumps(asdict(report), indent=2))
        return 0
    except DatabaseTransferError as exc:
        print(str(exc))
        return 1
    except Exception:
        # Driver exceptions may contain URLs or raw SQL data. Preserve only a
        # stable CLI failure; the transaction has already rolled back.
        print("database_transfer_failed")
        return 1
    finally:
        if source is not None:
            source.dispose()
        if target is not None:
            target.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
