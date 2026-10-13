"""Production-browser corpus: exact cardinality, completed local thumbnails, no imports.

Run only against the disposable root created by the startup launcher.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import DerivativeKind, User
from app.db.session import get_engine
from app.modules.administration import setup_bootstrap
from app.modules.library.multipart_models import replace_parts
from app.modules.search.passages import sync_subject
from app.schemas.multipart_models import MultipartPartWrite
from app.schemas.setup import SetupRequest
from tests.factories import (
    build_collection,
    build_derivative,
    build_file,
    build_model,
    build_multipart_model,
    build_tag,
    tag_model,
)


def seed(distribution: str) -> None:
    if distribution not in {"distributed", "dense", "rich"}:
        raise ValueError(f"unknown startup distribution: {distribution}")
    frontend = Path(__file__).resolve().parents[3] / "frontend"
    allowed_roots = {
        frontend / "tests/performance/.startup-data",
        frontend / ".startup-results/library-acceptance/distributed",
        frontend / ".startup-results/library-acceptance/rich",
    }
    if Path(settings.data_root).resolve() not in allowed_roots:
        raise RuntimeError("startup fixtures require a dedicated disposable data root")
    with Session(get_engine()) as session:
        ownership = setup_bootstrap.claim(
            session,
            SetupRequest(
                username="admin", password="admin1234", storage_backend="local"
            ),
        )
        if not ownership.storage_ready:
            raise RuntimeError("startup fixture storage was not activated")
        collections = [
            build_collection(session, f"Collection {index + 1:02d}")
            for index in range(20 if distribution == "rich" else 27)
        ]
        if distribution == "rich":
            parent = collections[2]
            for index in range(7):
                parent = build_collection(
                    session, f"Depth {index + 1:02d}", parent=parent
                )
                collections.append(parent)
        tag = build_tag(session, "benchmark")
        models = []
        for index in range(93 if distribution == "rich" else 91):
            collection = (
                collections[index % 27]
                if distribution == "distributed"
                else collections[0]
                if index < 90
                else collections[-1]
                if distribution == "rich"
                else collections[1]
            )
            model = build_model(
                session,
                name=f"Benchmark model {index + 1:03d}",
                collection=collection,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            models.append(model)
            content = f"solid benchmark_{index}\nendsolid benchmark_{index}\n".encode()
            artifact = build_file(
                session,
                model,
                filename="part.stl",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
            original = Path(settings.data_dir) / artifact.path
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(content)
            thumbnail_key = f"{artifact.id}.webp"
            Image.new("RGB", (160, 160), (50 + index, 110, 170)).save(
                Path(settings.thumb_dir) / thumbnail_key, format="WEBP"
            )
            artifact.thumbnail_path = thumbnail_key
            model.thumbnail_path = thumbnail_key
            model.thumbnail_file_id = artifact.id
            session.add(artifact)
            session.add(model)
            session.commit()
            build_derivative(session, artifact, DerivativeKind.THUMBNAIL)
            build_derivative(session, artifact, DerivativeKind.METADATA)
            if index % 2 == 0:
                tag_model(session, model, tag)

        if distribution == "rich":
            user = session.exec(select(User)).one()
            for name, indexes in [
                ("AAA composition", [0, 29]),
                ("ZZZ composition", [29, 89]),
            ]:
                multipart = build_multipart_model(
                    session, name, collection=collections[0]
                )
                replace_parts(
                    session,
                    user,
                    multipart,
                    [
                        MultipartPartWrite(
                            name=f"Part {part}", model_ids=[models[index].id]
                        )
                        for part, index in enumerate(indexes)
                    ],
                )
                multipart.updated_at = datetime(2025, 1, 1, tzinfo=UTC)
                session.add(multipart)
                session.commit()
        # A settled production corpus includes local lexical passages. Rendering
        # factory Models alone would benchmark an empty, unfinished search index.
        for model in models:
            sync_subject(session, SearchSubject(SubjectType.MODEL, model.id))
        session.commit()
        manifest = {
            "models": len(models),
            "model_rows": [
                {
                    "id": model.id,
                    "name": model.name,
                    "collection_id": model.collection_id,
                    "media": "available",
                }
                for model in models
            ],
            "collections": [
                {"id": collection.id, "path": collection.path, "name": collection.name}
                for collection in collections
            ],
            "dense": collections[0].path,
            "deep": collections[-1].path,
            "multipart": 2 if distribution == "rich" else 0,
        }
        (Path(settings.data_root) / "corpus.json").write_text(
            json.dumps(manifest, indent=2)
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--distribution", choices=("distributed", "dense", "rich"), required=True
    )
    seed(parser.parse_args().distribution)
