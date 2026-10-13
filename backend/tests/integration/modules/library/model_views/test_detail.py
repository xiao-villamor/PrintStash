"""Editable provenance receipts preserve identity without a per-source read loop."""

import pytest
from sqlmodel import Session

from app.core.errors import OperationError
from app.modules.library.model_views.detail import provenance_detail
from tests._statements import StatementLog
from tests.factories import build_model
from tests.factories.provenance import build_cover, build_provenance_source


class TestProvenanceDetail:
    def test_refuses_absent_model(self, db_session: Session):
        with pytest.raises(OperationError, match="model_not_found"):
            provenance_detail(db_session, 999999)

    def test_scopes_cover_metadata_to_model(self, db_session: Session):
        model = build_model(db_session)
        source = build_provenance_source(db_session, model)
        cover = build_cover(db_session, source)
        other = build_model(db_session)
        other_source = build_provenance_source(db_session, other)
        other_cover = build_cover(db_session, other_source)

        result = provenance_detail(db_session, model.id)

        assert [entry.id for entry in result.sources] == [source.id]
        assert result.sources[0].cover is not None
        assert result.sources[0].cover.id == cover.id
        assert result.sources[0].cover.id != other_cover.id

    def test_keeps_query_count_fixed(
        self, db_session: Session, sql_statements: StatementLog
    ):
        model = build_model(db_session)
        model_id = model.id
        build_cover(db_session, build_provenance_source(db_session, model))
        with sql_statements.recording():
            first = provenance_detail(db_session, model_id)
        initial_count = sql_statements.count
        assert len(first.sources) == 1
        for _ in range(9):
            build_cover(db_session, build_provenance_source(db_session, model))

        with sql_statements.recording():
            expanded = provenance_detail(db_session, model_id)

        assert len(expanded.sources) == 10
        assert all(source.cover is not None for source in expanded.sources)
        assert sql_statements.count == initial_count
