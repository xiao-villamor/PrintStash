"""Restoration window pages preserve the cursor contract on PostgreSQL."""

from sqlmodel import Session

from app.modules.library import outliner
from app.schemas.outliner import OutlinerQuery, OutlinerRestoreQuery
from tests.factories import build_collection, build_model, build_user

from .test_collection_tree import pg_session as pg_session


class TestRestore:
    def test_restores_with_postgresql(self, pg_session: Session):
        user = build_user(pg_session, superuser=True)
        root = build_collection(pg_session, "Root")
        child = build_collection(pg_session, "Child", parent=root)
        first = build_model(pg_session, "A", collection=child)
        second = build_model(pg_session, "B", collection=child)

        restored = outliner.restore(
            pg_session,
            user,
            OutlinerRestoreQuery(
                expanded_paths=[root.path, child.path],
                limit=1,
            ),
        )
        page = next(
            level.page for level in restored.entries if level.collection_id == child.id
        )
        next_page = outliner.entries(
            pg_session,
            user,
            OutlinerQuery(
                collection_id=child.id,
                cursor=page.next_cursor,
                limit=1,
            ),
        )

        assert [node.id for node in restored.collections[0].page.items] == [root.id]
        assert restored.collections[0].page.items[0].subtree_entry_count == 2
        assert [entry.id for entry in page.items + next_page.items] == [
            first.id,
            second.id,
        ]
        assert next_page.next_cursor is None
