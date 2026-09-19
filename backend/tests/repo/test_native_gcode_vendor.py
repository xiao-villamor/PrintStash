"""The native G-code dependency graph stays pinned, minimal, and auditable."""

from __future__ import annotations

import tomllib

from tests.paths import REPO_ROOT

RUST = REPO_ROOT / "backend" / "rust"


class TestNativeGcodeVendor:
    def test_pins_the_verified_direct_dependency_graph(self) -> None:
        gcode = tomllib.loads((RUST / "gcode-core" / "Cargo.toml").read_text())
        adapter = tomllib.loads((RUST / "libbgcode-sys" / "Cargo.toml").read_text())

        assert gcode["dependencies"]["regex"] == "=1.13.1"
        assert adapter["dependencies"]["cxx"] == "=1.0.202"
        assert adapter["dependencies"]["libz-sys"] == {
            "version": "=1.1.29",
            "features": ["static"],
        }
        assert adapter["build-dependencies"]["cc"] == "=1.4.6"
        assert adapter["build-dependencies"]["cxx-build"] == "=1.0.202"

    def test_builds_only_the_sources_needed_for_bounded_inspection(self) -> None:
        build = (RUST / "libbgcode-sys" / "build.rs").read_text()

        assert 'file("vendor/libbgcode/core/core.cpp")' in build
        assert 'file("vendor/heatshrink/heatshrink_decoder.c")' in build
        assert "binarize.cpp" not in build
        assert "meatpack.cpp" not in build
        assert "heatshrink_encoder.c" not in build

    def test_records_immutable_upstream_provenance(self) -> None:
        vendor = (RUST / "libbgcode-sys" / "VENDOR.md").read_text()

        assert "d4da9073616d70a43c151e8c1d7fbff879d2e08a" in vendor
        assert (
            "d26778992d44b7cfaab99b542d871355f7a66210a512a5503a5b8a941a7d409b" in vendor
        )
        assert "Heatshrink" in vendor and "v0.4.1" in vendor and "ISC" in vendor
        assert "libz-sys" in vendor and "Zlib license" in vendor

    def test_packages_the_adapter_in_the_existing_native_wheel(self) -> None:
        dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text()

        assert "COPY rust/gcode-core ./gcode-core" in dockerfile
        assert "COPY rust/libbgcode-sys ./libbgcode-sys" in dockerfile
        assert "g++ python3 python3-venv" in dockerfile
