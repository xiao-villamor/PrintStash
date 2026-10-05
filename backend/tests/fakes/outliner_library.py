"""Seed the real-browser throwaway database using the shared scale factory."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlmodel import Session, create_engine

from tests.factories import build_collection, build_model
from tests.factories.library_scale import build_library_at_scale


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_root", type=Path)
    args = parser.parse_args()
    database = args.data_root / "db/printstash.sqlite"
    if not database.is_file():
        raise ValueError("real_browser_database_missing")
    engine = create_engine(f"sqlite:///{database}")
    with Session(engine) as session:
        root = build_collection(session, f"Outliner browser {uuid4().hex[:8]}")
        seeded = build_library_at_scale(session, under=root, collections=1, models=501)
        from app.db.models import Collection

        folder = session.get(Collection, seeded.collection_ids[0])
        if folder is None:
            raise ValueError("seeded_collection_missing")
        target = build_model(
            session,
            "Zebra paginated target",
            collection=folder,
            hash=hashlib.sha256(root.name.encode()).hexdigest(),
            slug=f"outliner-target-{root.id}",
        )
        destination = build_collection(session, "Move destination", parent=root)
        print(
            json.dumps(
                {
                    "root": {"id": root.id, "name": root.name},
                    "folder": {
                        "id": folder.id,
                        "name": folder.name,
                        "path": folder.path,
                    },
                    "target": {"id": target.id, "name": target.name},
                    "destination": {
                        "id": destination.id,
                        "name": destination.name,
                        "path": destination.path,
                    },
                }
            )
        )
    engine.dispose()


if __name__ == "__main__":
    main()
