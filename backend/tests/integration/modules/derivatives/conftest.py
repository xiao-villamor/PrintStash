from __future__ import annotations

import pytest
from sqlmodel import Session

from app.modules.storage.storage_backend.runtime import get_backend


@pytest.fixture
def remove_blob_key():
    """Empty a storage key, the way a delete outside PrintStash would."""

    def remove(key: str) -> None:
        direct = get_backend().direct_path(key)
        assert direct is not None
        direct.unlink(missing_ok=True)

    return remove


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgresql", marks=pytest.mark.postgres, id="postgresql"),
    ]
)
def publication_engine(request, tmp_path_factory):
    """Independent connections with production locks, on both supported engines."""
    from sqlalchemy import event
    from sqlmodel import SQLModel, create_engine

    from app.db.session import _set_sqlite_pragmas
    from app.db.url import normalize_database_url
    from tests.containers import fresh_postgres_database
    from tests.factories import build_system_config

    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path_factory.mktemp('publication') / 'vault.sqlite'}"
        engine = create_engine(url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _set_sqlite_pragmas)
    else:
        engine = create_engine(
            normalize_database_url(fresh_postgres_database("derivative_publication"))
        )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        build_system_config(session)
    try:
        yield engine
    finally:
        engine.dispose()
