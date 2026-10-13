"""A toolpath committed between delivery reads is served with the normal limit."""

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.api.toolpath_response import toolpath_response
from app.core.config import _overlay
from app.db.models import DerivativeKind, FileType
from app.db.session import _set_sqlite_pragmas
from app.modules.storage.storage_backend.runtime import get_backend
from tests import factories


@pytest.fixture
def published_during_lookup(tmp_path):
    database_path = tmp_path / "publication.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as reader:
            file = factories.build_file(
                reader,
                factories.build_model(reader),
                filename="plate.bgcode",
                ftype=FileType.GCODE,
            )

            def respond(data):
                key = "_derivatives/concurrent-toolpath.gcode"
                get_backend().write_bytes(data, key)
                published = False

                def commit_after_lookup(
                    _connection, _cursor, statement, _parameters, _context, _many
                ):
                    nonlocal published
                    if (
                        published
                        or "artifact_derivatives.storage_key IS NOT NULL"
                        not in statement
                    ):
                        return
                    published = True
                    # The first SELECT has executed with no published output.
                    # Commit a real producer result through a second connection
                    # before the delivery service reads the current recipe row.
                    with Session(engine) as writer:
                        factories.build_derivative(
                            writer, file, DerivativeKind.TOOLPATH, storage_key=key
                        )

                event.listen(engine, "after_cursor_execute", commit_after_lookup)
                try:
                    return toolpath_response(reader, file)
                finally:
                    event.remove(engine, "after_cursor_execute", commit_after_lookup)
                    assert published

            yield respond
    finally:
        engine.dispose()


class TestConcurrentPublication:
    def test_serves_a_toolpath_published_during_the_lookup(
        self, published_during_lookup
    ):
        data = b"G1 X42 E1\n"
        response = published_during_lookup(data)
        assert (response.status_code, response.body) == (200, data)
        assert response.headers["cache-control"] == "private, no-store"

    def test_refuses_an_oversized_concurrent_publication(self, published_during_lookup):
        _overlay["toolpath_output_max_mb"] = 1
        with pytest.raises(HTTPException, match="toolpath_output_too_large") as raised:
            published_during_lookup(b"G1\n" * 400_000)
        assert raised.value.status_code == 413
