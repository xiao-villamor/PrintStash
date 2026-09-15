"""Extension loading closes on every connection, including async and failures."""

import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import _overlay
from app.db.session import create_async_engine_for_db
from app.db.vector_extensions import load_sqlite_vector_extension


class TestVectorExtensions:
    def test_disables_sqlite_extension_loading_after_connect(self):
        with sqlite3.connect(":memory:") as connection:
            assert load_sqlite_vector_extension(connection)
            assert connection.execute("SELECT vec_version()").fetchone()[0] == "v0.1.6"
            with pytest.raises(sqlite3.OperationalError, match="not authorized"):
                connection.execute("SELECT load_extension('untrusted')")

    def test_disables_loading_after_failure(self, monkeypatch):
        import sqlite_vec

        def fail(connection):
            raise sqlite3.OperationalError("cannot load")
        monkeypatch.setattr(sqlite_vec, "load", fail)
        with sqlite3.connect(":memory:") as connection:
            assert not load_sqlite_vector_extension(connection)
            with pytest.raises(sqlite3.OperationalError, match="not authorized"):
                connection.execute("SELECT load_extension('untrusted')")
            assert connection.execute("SELECT 42").fetchone() == (42,)

    @pytest.mark.asyncio
    async def test_disables_loading_after_async_connect(self, monkeypatch):
        monkeypatch.setitem(_overlay, "search_native_vectors_enabled", True)
        engine = create_async_engine_for_db("sqlite:///:memory:")
        try:
            async with engine.connect() as connection:
                assert (await connection.execute(text("SELECT vec_version()"))).scalar_one() == "v0.1.6"
                with pytest.raises(DBAPIError, match="not authorized"):
                    await connection.execute(text("SELECT load_extension('untrusted')"))
        finally:
            await engine.dispose()
