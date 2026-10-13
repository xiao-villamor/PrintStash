"""A disposable application parent warms the original CC0 ONNX fixture."""

import json
import os
import sys
from pathlib import Path


def main() -> None:
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    from printstash_core.inference import EmbeddingInput
    from sqlmodel import create_engine

    from app.core.config import _overlay
    from app.db.session import SQLiteSessionFactory
    from app.modules.inference.local import LocalEmbeddingProvider
    from app.modules.inference.worker_pool import pool
    from app.runtime import inference_resources
    from app.runtime.native_admission import LocalResourcePool

    assets, directory = map(Path, sys.argv[1:])
    _overlay.update(
        embedding_memory_budget_fraction=0.25,
        mesh_memory_budget_fraction=0.5,
        embedding_resident_workers=1,
        embedding_worker_memory_mb=1024,
    )
    inference_resources.bind_pool(LocalResourcePool(directory))
    sessions = SQLiteSessionFactory(create_engine("sqlite://"))
    provider = LocalEmbeddingProvider(sessions, assets, "two-tower-contract", 1)
    result = provider.embed((EmbeddingInput("text", text="red"),), provider.space)
    worker = pool._workers[provider._worker_key()].process
    assert worker.stdin is not None
    print(
        json.dumps(
            {
                "worker_pid": worker.pid,
                "worker_stdin_fd": worker.stdin.fileno(),
                "vector": result[0],
            }
        ),
        file=protocol,
        flush=True,
    )
    sys.stdin.buffer.read()
    pool.close()
    sessions.dispose()


if __name__ == "__main__":
    main()
