"""Provision original, tiny ONNX contract assets for the real-browser lane.

The retrieval quality gates use separately measured real BGE vectors. This
export makes browser/runtime wiring reproducible without a network download.
"""

import argparse
from pathlib import Path

from tests.factories.embeddings import (
    local_embedding_assets,
    point_embedding_assets,
    text_embedding_assets,
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    text_embedding_assets(arguments.directory / "cache" / "text")
    local_embedding_assets(arguments.directory / "cache" / "clip")
    point_embedding_assets(arguments.directory / "cache" / "point")
