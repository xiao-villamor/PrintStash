"""Unit tests for taxonomy resolution services."""

from __future__ import annotations

from app.services.taxonomy import parse_tag_input


class TestParseTagInput:
    def test_parse_simple(self) -> None:
        assert parse_tag_input("bracket, functional, PLA") == [
            "bracket",
            "functional",
            "PLA",
        ]

    def test_parse_with_whitespace(self) -> None:
        assert parse_tag_input("  bracket , functional ,  PLA  ") == [
            "bracket",
            "functional",
            "PLA",
        ]

    def test_parse_empty(self) -> None:
        assert parse_tag_input("") == []

    def test_parse_none(self) -> None:
        assert parse_tag_input(None) == []

    def test_parse_blank_tags_skipped(self) -> None:
        assert parse_tag_input("bracket,,functional") == ["bracket", "functional"]


class TestResolveTags:
    def test_resolve_creates_new_tags(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_tags

        tags = resolve_or_create_tags(db_session, ["prototype", "v2"])
        assert len(tags) == 2
        names = {t.name for t in tags}
        assert names == {"prototype", "v2"}

    def test_resolve_skips_blank_and_duplicate_names(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_tags

        tags = resolve_or_create_tags(db_session, ["  ", "Bracket", "bracket"])
        names = {t.name for t in tags}
        assert names == {"Bracket"}

    def test_resolve_reuses_existing_tags(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_tags

        resolve_or_create_tags(db_session, ["shared"])
        tags = resolve_or_create_tags(db_session, ["shared", "new"])
        assert len(tags) == 2
        names = {t.name for t in tags}
        assert names == {"shared", "new"}

    def test_resolve_revives_a_trashed_tag(self, db_session) -> None:
        """Re-tagging with a soft-deleted tag's name must revive that row, not
        link the model to a dead tag list_tags() hides (mirrors collections)."""
        from app.core.time import utcnow
        from app.services.taxonomy import resolve_or_create_tags

        (tag,) = resolve_or_create_tags(db_session, ["functional"])
        tag.deleted_at = utcnow()
        db_session.add(tag)
        db_session.commit()

        (revived,) = resolve_or_create_tags(db_session, ["Functional"])

        assert revived.id == tag.id  # same row, by slug
        assert revived.deleted_at is None  # brought back to life
        db_session.refresh(tag)
        assert tag.deleted_at is None


class TestResolveCollections:
    def test_resolve_creates_hierarchy(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_collection

        cat = resolve_or_create_collection(db_session, "Functional/Brackets")
        assert cat is not None
        assert cat.name == "Brackets"
        assert cat.path == "functional/brackets"

    def test_resolve_reuses_existing(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_collection

        first = resolve_or_create_collection(db_session, "Functional/Brackets")
        second = resolve_or_create_collection(db_session, "Functional/Brackets")
        assert first is not None and second is not None
        assert first.id == second.id

    def test_resolve_empty_returns_none(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_collection

        assert resolve_or_create_collection(db_session, "") is None
        assert resolve_or_create_collection(db_session, None) is None

    def test_resolve_whitespace_only_segments_returns_none(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_collection

        assert resolve_or_create_collection(db_session, "  /// ") is None

    def test_resolve_revives_a_trashed_collection(self, db_session) -> None:
        from app.core.time import utcnow
        from app.services.taxonomy import resolve_or_create_collection

        cat = resolve_or_create_collection(db_session, "Archive")
        assert cat is not None
        cat.deleted_at = utcnow()
        db_session.add(cat)
        db_session.commit()

        revived = resolve_or_create_collection(db_session, "Archive")
        assert revived is not None
        assert revived.id == cat.id
        assert revived.deleted_at is None


class TestResolveCollectionInTransaction:
    def test_creates_hierarchy_without_committing(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_collection_in_transaction

        cat = resolve_or_create_collection_in_transaction(
            db_session, "Functional/Brackets"
        )
        assert cat is not None
        assert cat.path == "functional/brackets"
        # visible within the same uncommitted transaction
        db_session.commit()
        assert cat.id is not None

    def test_empty_returns_none(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_collection_in_transaction

        assert resolve_or_create_collection_in_transaction(db_session, "   ") is None

    def test_reuses_live_row(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_collection_in_transaction

        first = resolve_or_create_collection_in_transaction(db_session, "Reused")
        db_session.commit()
        second = resolve_or_create_collection_in_transaction(db_session, "Reused")
        assert first is not None and second is not None
        assert first.id == second.id

    def test_revives_trashed_row(self, db_session) -> None:
        from app.core.time import utcnow
        from app.services.taxonomy import resolve_or_create_collection_in_transaction

        cat = resolve_or_create_collection_in_transaction(db_session, "TrashMe")
        db_session.commit()
        assert cat is not None
        cat.deleted_at = utcnow()
        db_session.add(cat)
        db_session.commit()

        revived = resolve_or_create_collection_in_transaction(db_session, "TrashMe")
        assert revived is not None
        assert revived.id == cat.id
        assert revived.deleted_at is None


class TestListAndDescendantPaths:
    def test_list_collections_service(self, db_session) -> None:
        from app.services.taxonomy import list_collections, resolve_or_create_collection

        resolve_or_create_collection(db_session, "Alpha")
        resolve_or_create_collection(db_session, "Beta")
        cats = list_collections(db_session)
        paths = {c.path for c in cats}
        assert {"alpha", "beta"}.issubset(paths)

    def test_collection_descendant_paths(self, db_session) -> None:
        from app.services.taxonomy import (
            collection_descendant_paths,
            resolve_or_create_collection,
        )

        resolve_or_create_collection(db_session, "Root")
        resolve_or_create_collection(db_session, "Root/Child")
        resolve_or_create_collection(db_session, "RootOther")

        paths = collection_descendant_paths(db_session, "root")
        assert set(paths) == {"root", "root/child"}


class TestResolveTagsInTransaction:
    def test_creates_and_dedupes(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_tags_in_transaction

        tags = resolve_or_create_tags_in_transaction(
            db_session, ["Bracket", "bracket", "  ", "New"]
        )
        db_session.commit()
        names = {t.name for t in tags}
        assert names == {"Bracket", "New"}

    def test_reuses_live_tag(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_tags_in_transaction

        first = resolve_or_create_tags_in_transaction(db_session, ["shared"])
        db_session.commit()
        second = resolve_or_create_tags_in_transaction(db_session, ["shared"])
        assert first[0].id == second[0].id

    def test_revives_trashed_tag(self, db_session) -> None:
        from app.core.time import utcnow
        from app.services.taxonomy import resolve_or_create_tags_in_transaction

        (tag,) = resolve_or_create_tags_in_transaction(db_session, ["gone"])
        db_session.commit()
        tag.deleted_at = utcnow()
        db_session.add(tag)
        db_session.commit()

        (revived,) = resolve_or_create_tags_in_transaction(db_session, ["gone"])
        assert revived.id == tag.id
        assert revived.deleted_at is None


class TestListTagsService:
    def test_list_tags_service(self, db_session) -> None:
        from app.services.taxonomy import list_tags, resolve_or_create_tags

        resolve_or_create_tags(db_session, ["z-tag", "a-tag"])
        tags = list_tags(db_session)
        names = [t.name for t in tags]
        assert names == sorted(names)
        assert {"z-tag", "a-tag"}.issubset(set(names))
