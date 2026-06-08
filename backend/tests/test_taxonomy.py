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

    def test_resolve_reuses_existing_tags(self, db_session) -> None:
        from app.services.taxonomy import resolve_or_create_tags

        resolve_or_create_tags(db_session, ["shared"])
        tags = resolve_or_create_tags(db_session, ["shared", "new"])
        assert len(tags) == 2
        names = {t.name for t in tags}
        assert names == {"shared", "new"}


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
