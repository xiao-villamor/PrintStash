"""Prepare one real external copy and expose its path before abrupt parent death."""

import hashlib
import json
import sys
from pathlib import Path

from app.bootstrap.native_resources import configure
from app.modules.media.source_preparation import reserve_sources
from app.modules.storage.artifact_content import resolve
from tests.factories import detached_file


def main():
    root, source = map(Path, sys.argv[1:])
    configure(root)
    payload = source.read_bytes()
    artifact = detached_file(
        model_id=1,
        path=str(source),
        original_filename=source.name,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        is_external=True,
    )
    with reserve_sources((resolve(artifact),)) as batch:
        with batch.materialize(capacity_claimed=True) as paths:
            print(json.dumps({"path": str(paths[0])}), flush=True)
            sys.stdin.read(1)


if __name__ == "__main__":
    main()
