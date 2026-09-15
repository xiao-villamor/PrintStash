"""Subject IDs from unrelated tables cannot become a caption's Model input."""

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType

from app.db.models import FileType
from app.modules.search.caption_source import source


class TestSource:
    @pytest.mark.parametrize(
        "kind",
        [SubjectType.COLLECTION, SubjectType.MULTIPART_MODEL, SubjectType.DOCUMENT],
    )
    def test_keeps_source_identities_typed(
        self, db_session, make_model, make_file, kind
    ):
        model = make_model()
        artifact = make_file(model, file_type=FileType.STL, sha256="a" * 64)
        assert (
            source(db_session, SearchSubject(SubjectType.MODEL, model.id)).id
            == artifact.id
        )
        assert source(db_session, SearchSubject(kind, model.id)) is None
