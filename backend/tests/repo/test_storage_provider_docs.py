"""The published storage-provider reference stays generated from its registry."""

from pathlib import Path

from app.modules.storage.storage_providers import render_storage_provider_docs


class TestStorageProviderDocumentation:
    def test_matches_registry(self) -> None:
        docs = Path(__file__).parents[3] / "docs" / "storage-providers.md"
        assert docs.read_text(encoding="utf-8") == render_storage_provider_docs()

    def test_role_matrix_exposes_operational_facts(self) -> None:
        docs = render_storage_provider_docs()

        assert (
            "| Provider | Transport | Vault | Library source | Backup destination | "
            "Runtime | Support | Expected tier | Browser delivery |" in docs
        )
        assert (
            "| [Google Drive](#gdrive) | gdrive | — | ✓ | ✓ | Full image | Beta | "
            "Unguarded | Proxy |" in docs
        )
        assert (
            "| [MinIO](#minio) | s3 | ✓ | ✓ | ✓ | All images | Beta | Guarded | "
            "Signed GET candidate; proxy fallback |" in docs
        )

    def test_each_provider_documents_operational_limits(self) -> None:
        docs = render_storage_provider_docs()

        assert docs.count("Runtime packaging: **") == 23
        assert docs.count("Large objects: ") == 23
        assert docs.count("Supported roles: ") == 23
        assert (
            "Runtime packaging: **Full image**; requires OpenDAL with WebDAV support."
            in docs
        )
        assert (
            "Large objects: multipart or bounded streaming writes and range reads."
            in docs
        )

    def test_documents_independent_staging_capabilities(self) -> None:
        docs = render_storage_provider_docs()
        assert "components.storage.diagnostics.staging" in docs
        assert "Unraid `/mnt/user` (SHFS/FUSE)" in docs
        assert "A staging copy warning does not change the Vault's safety tier." in docs
