"""Optional tag filters preserve the unfiltered library selection."""

import pytest
from sqlmodel import select

from app.db.models import Model
from app.db.scopes import live
from app.modules.library.library_search import apply_library_search


class TestApplyLibrarySearch:
    @pytest.mark.parametrize("tag_slugs", [[], ["", "  "]], ids=["absent", "blank"])
    def test_preserves_models_without_an_effective_tag_filter(
        self, db_session, make_model, make_tag, tag_model, tag_slugs
    ):
        tagged = make_model("Tagged")
        plain = make_model("Plain")
        tag_model(tagged, make_tag("hardware"))

        statement = apply_library_search(
            select(Model).where(live(Model), Model.id.in_([tagged.id, plain.id])),
            query=None,
            tag_slugs=tag_slugs,
        )
        rows = db_session.exec(statement).all()

        assert {row.id for row in rows} == {tagged.id, plain.id}
