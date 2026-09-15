"""Deterministic SQLite instruction counts for bounded-query regressions."""

from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class Work:
    instructions: int = 0


@contextmanager
def sqlite_work(session):
    connection = session.connection().connection.driver_connection
    work = Work()

    def progress():
        work.instructions += 100
        return 0

    connection.set_progress_handler(progress, 100)
    try:
        yield work
    finally:
        connection.set_progress_handler(None, 0)
