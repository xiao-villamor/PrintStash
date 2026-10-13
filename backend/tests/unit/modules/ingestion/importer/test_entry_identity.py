"""Logical input identity is independent of disposable paths and provider ordering."""

from pathlib import Path

from app.modules.ingestion.importer import (
    direct_entry_spec,
    entry_spec,
)


class TestEntryIdentity:
    def test_preserves_identity_when_staging_path_changes(self) -> None:
        first = entry_spec(
            (Path("/first/temporary.gcode"), "part.gcode"), source_id="provider:item:1"
        )
        retry = entry_spec(
            (Path("/retry/other.gcode"), "part.gcode"), source_id="provider:item:1"
        )
        assert first.identity == retry.identity
        assert first.key == retry.key
        assert first.descriptor == retry.descriptor

    def test_distinguishes_equal_names_from_different_sources(self) -> None:
        first = entry_spec(
            (Path("/temporary/one"), "part.gcode"), source_id="provider:item:1"
        )
        second = entry_spec(
            (Path("/temporary/two"), "part.gcode"), source_id="provider:item:2"
        )
        assert first.display_name == second.display_name
        assert first.identity != second.identity
        assert first.key != second.key


class TestSelectedSourceIdentity:
    def test_plans_a_source_without_accessing_bytes(self, monkeypatch) -> None:
        def forbid_stat(*args, **kwargs):
            raise AssertionError("planning must not open source bytes")

        monkeypatch.setattr(Path, "stat", forbid_stat)
        spec = direct_entry_spec("provider:item", "selected:1", "part.gcode")
        retry = direct_entry_spec("provider:item", "selected:1", "part.gcode")
        assert spec == retry
        assert spec.descriptor.source_id == "provider:item"
        assert spec.size_bytes is None
