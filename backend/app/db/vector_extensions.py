"""Optional trusted SQLite extension loading for sync and async pools."""

from __future__ import annotations

import sqlite3


def load_sqlite_vector_extension(connection) -> bool:
    """Load only the installed wheel, always closing the extension-loading gate.

    No path or binary comes from user input. Missing/unsupported extension is a
    capability result, not a failure to open the durable application database.
    SQLAlchemy async adapters expose run_async for the underlying aiosqlite API.
    """
    try:
        import sqlite_vec

        if hasattr(connection, "run_async"):

            async def load(raw):
                try:
                    await raw.enable_load_extension(True)
                    await raw.load_extension(sqlite_vec.loadable_path())
                finally:
                    await raw.enable_load_extension(False)

            connection.run_async(load)
        else:
            try:
                connection.enable_load_extension(True)
                sqlite_vec.load(connection)
            finally:
                connection.enable_load_extension(False)
        return True
    except (ImportError, AttributeError, OSError, sqlite3.Error):
        return False
