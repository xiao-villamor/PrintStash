"""The published storage-provider reference stays generated from its registry."""

from pathlib import Path

from app.services.storage_providers import render_storage_provider_docs


class TestStorageProviderDocumentation:
    def test_matches_registry(self) -> None:
        docs = Path(__file__).parents[3] / "docs" / "storage-providers.md"
        assert docs.read_text(encoding="utf-8") == render_storage_provider_docs()
