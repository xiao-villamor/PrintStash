"""Reusable native cores remain part of the production wheel build."""

from __future__ import annotations

from tests.paths import REPO_ROOT


class TestNativeCorePackaging:
    def test_packages_acquisition_core_in_the_native_wheel(self) -> None:
        dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text()

        assert "COPY rust/acquisition-core ./acquisition-core" in dockerfile

    def test_packages_archive_core_in_the_native_wheel(self) -> None:
        dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text()

        assert "COPY rust/archive-core ./archive-core" in dockerfile

    def test_packages_similarity_core_in_the_native_wheel(self) -> None:
        dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text()

        assert "COPY rust/similarity-core ./similarity-core" in dockerfile
