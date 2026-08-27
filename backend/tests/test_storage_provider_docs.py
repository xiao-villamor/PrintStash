from pathlib import Path

from app.services.storage_providers import render_storage_provider_docs


def test_storage_provider_documentation_matches_registry() -> None:
    docs = Path(__file__).parents[2] / "docs" / "storage-providers.md"
    assert docs.read_text(encoding="utf-8") == render_storage_provider_docs()
