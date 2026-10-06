"""Production-browser corpus: exact cardinality, completed local thumbnails, no imports.

Run only against the disposable root created by the startup launcher.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from PIL import Image
from sqlmodel import Session

from app.core.config import settings
from app.db.models import DerivativeKind
from app.db.session import get_engine
from app.modules.administration import setup_bootstrap
from app.schemas.setup import SetupRequest
from tests.factories import (
    build_collection,
    build_derivative,
    build_file,
    build_model,
    build_tag,
    tag_model,
)


def seed(distribution: str) -> None:
    if distribution not in {"distributed", "dense"}:
        raise ValueError(f"unknown startup distribution: {distribution}")
    expected_root = (
        Path(__file__).resolve().parents[3] / "frontend/tests/performance/.startup-data"
    )
    if Path(settings.data_root).resolve() != expected_root:
        raise RuntimeError(
            "startup fixtures require the dedicated disposable data root"
        )
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
            for index in range(27)
        ]
        tag = build_tag(session, "benchmark")
        for index in range(91):
            collection = (
                collections[index % 27]
                if distribution == "distributed"
                else collections[0]
                if index < 90
                else collections[1]
            )
            model = build_model(
                session, name=f"Benchmark model {index + 1:03d}", collection=collection
            )
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--distribution", choices=("distributed", "dense"), required=True
    )
    seed(parser.parse_args().distribution)
