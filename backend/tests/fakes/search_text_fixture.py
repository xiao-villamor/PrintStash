"""Regenerate real-model regression vectors from explicitly preplaced assets."""

import argparse
import base64
import csv
import hashlib
import json
import struct
import tempfile
from pathlib import Path

from printstash_core.inference import EmbeddingInput
from printstash_core.inference.context import InferenceContext
from printstash_core.search.passages import PassageContent, render_passages
from sqlmodel import SQLModel, create_engine

from app.db.session import SQLiteSessionFactory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.model_registry import require
from app.modules.inference.worker_pool import pool
from tests.paths import FIXTURES_DIR


def generate(directory: Path, output: Path):
    corpus_root = FIXTURES_DIR / "search"
    corpus = json.loads((corpus_root / "corpus.json").read_text())
    with (corpus_root / "queries.csv").open() as stream:
        queries = list(csv.DictReader(stream))
    entry = require("bge-small-en-v1.5")
    with tempfile.TemporaryDirectory(prefix="printstash-text-evaluation-") as temporary:
        engine = create_engine(f"sqlite:///{temporary}/compute.sqlite")
        SQLModel.metadata.create_all(engine)
        provider = LocalEmbeddingProvider(
            SQLiteSessionFactory(engine), directory, entry.manifest.model_key, 1
        )
        if provider.validate() != entry.manifest:
            raise ValueError("fixture_model_mismatch")
        inputs = [
            render_passages(
                PassageContent(title=row["name"], description=row["description"])
            )[0].text
            for row in corpus
        ]
        inputs += [provider.space.query_prefix + row["query"] for row in queries]
        records = {}
        for after in range(0, len(inputs), 8):
            batch = inputs[after : after + 8]
            vectors = provider.embed(
                tuple(EmbeddingInput("text", text=value) for value in batch),
                provider.space,
                context=InferenceContext.bounded(120, priority="background"),
            )
            for value, vector in zip(batch, vectors, strict=True):
                records[hashlib.sha256(value.encode()).hexdigest()] = base64.b64encode(
                    struct.pack(f"<{provider.space.dimension}f", *vector)
                ).decode()
        output.write_text(
            json.dumps(
                {
                    "space": provider.space.__dict__,
                    "corpus_sha256": hashlib.sha256(
                        (corpus_root / "corpus.json").read_bytes()
                    ).hexdigest(),
                    "queries_sha256": hashlib.sha256(
                        (corpus_root / "queries.csv").read_bytes()
                    ).hexdigest(),
                    "vectors": records,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        pool.close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    generate(arguments.model_dir, arguments.output)
