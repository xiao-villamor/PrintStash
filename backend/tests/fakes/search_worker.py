"""Standalone durable index worker for process-loss tests; uses real app services."""

import argparse
import time

import app.db.models  # noqa: F401 -- register the complete durable schema
from app.db.models import IndexGeneration
from app.db.session import get_session_factory
from app.modules.inference.transport import close_client
from app.modules.search.indexing import IndexProcessor
from app.runtime.jobs import reconcile_interrupted_jobs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=int, required=True)
    args = parser.parse_args()
    sessions = get_session_factory()
    processor = IndexProcessor(sessions)
    reconcile_interrupted_jobs()
    try:
        for _ in range(100):
            processor.work_one()
            with sessions.scoped_session() as session:
                generation = session.get(IndexGeneration, args.generation)
                if generation.state == "active":
                    print("active", flush=True)
                    return
            time.sleep(0.02)
        raise RuntimeError("generation did not activate")
    finally:
        close_client()


if __name__ == "__main__":
    main()
