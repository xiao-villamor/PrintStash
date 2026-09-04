"""Turning a chosen remote file into bytes on disk, keeping its identity intact.

An import selects *files*, not downloads. A single selection can arrive as one mesh or as
a zip holding twenty, and the whole point of this layer is that expanding the zip does not
lose track of which selection each member came from — otherwise a partial retry cannot
tell which of twenty entries actually failed, and the provenance record attaches a source
to the wrong bytes.

So every staged asset carries its `source_selection_id` back to the selection that asked
for it, plus a `result_key` that is `"self"` for a plain file and a per-entry key for
something out of a container. That pair is what makes a partial import retryable.

The one shape that is deliberately *not* expanded is a 3MF, which is a zip by construction
and a single model by intent. Unpacking one would turn one selection into a pile of XML.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from printstash_core.imports.contracts import CaptureManifestV2, ResolvedAsset

from app.core.config import _overlay
from app.services import inbox

STL = b"solid cube\nendsolid cube\n"


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


def _manifest(files: list[tuple[str, str]]) -> CaptureManifestV2:
    return CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": {
                "provider": "printables",
                "canonical_url": "https://www.printables.com/model/42",
                "source_item_id": "42",
                "source_revision": None,
                "adapter_version": "printables-v1",
                "fields": {},
            },
            "files": [
                {"id": file_id, "name": name, "file_type": "stl", "size": len(STL)}
                for file_id, name in files
            ],
            "selected_ids": [file_id for file_id, _ in files],
        }
    )


def _resolved(manifest: CaptureManifestV2, file_id: str, name: str) -> ResolvedAsset:
    return ResolvedAsset(
        manifest=manifest,
        source_selection_id=file_id,
        source_file_id=file_id,
        source_filename=name,
        download_url="https://example.test/download",
        source_item_id="42",
    )


@pytest.fixture
def staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    inbox.settings.incoming_dir.mkdir(parents=True, exist_ok=True)
    return inbox.settings.incoming_dir


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch, staging: Path):
    """Stand in for the one egress boundary: fetching the bytes."""

    def serve(data: bytes, name: str) -> None:
        async def download(_url: str) -> tuple[Path, str]:
            path = staging / name
            path.write_bytes(data)
            return path, name

        monkeypatch.setattr(inbox.importer, "download_to_staging", download)

    return serve


class TestDownloadResolvedAsset:
    def test_stages_a_plain_file_as_one_asset(self, downloads) -> None:
        import asyncio

        downloads(STL, "cube.stl")
        manifest = _manifest([("42:cube", "cube.stl")])

        assets = asyncio.run(
            inbox._download_resolved_asset(_resolved(manifest, "42:cube", "cube.stl"))
        )

        assert len(assets) == 1
        assert assets[0].result_key == "self"
        assert assets[0].staged_path.read_bytes() == STL

    def test_hashes_what_it_staged(self, downloads) -> None:
        import asyncio

        downloads(STL, "cube.stl")
        manifest = _manifest([("42:cube", "cube.stl")])

        assets = asyncio.run(
            inbox._download_resolved_asset(_resolved(manifest, "42:cube", "cube.stl"))
        )

        # The hash is what a later dedupe and a provenance link are keyed on.
        assert len(assets[0].blob_sha256) == 64

    def test_expands_a_zip_into_one_asset_per_entry(self, downloads) -> None:
        import asyncio

        downloads(_zip_bytes({"a.stl": STL, "b.stl": STL}), "bundle.zip")
        manifest = _manifest([("42:bundle", "bundle.zip")])

        assets = asyncio.run(
            inbox._download_resolved_asset(
                _resolved(manifest, "42:bundle", "bundle.zip")
            )
        )

        assert len(assets) == 2

    def test_keeps_every_expanded_entry_pointing_at_its_selection(
        self, downloads
    ) -> None:
        import asyncio

        downloads(_zip_bytes({"a.stl": STL, "b.stl": STL}), "bundle.zip")
        manifest = _manifest([("42:bundle", "bundle.zip")])

        assets = asyncio.run(
            inbox._download_resolved_asset(
                _resolved(manifest, "42:bundle", "bundle.zip")
            )
        )

        # Lose this and a partial retry cannot tell which of twenty entries failed.
        assert {asset.source_selection_id for asset in assets} == {"42:bundle"}

    def test_gives_each_expanded_entry_its_own_result_key(self, downloads) -> None:
        import asyncio

        downloads(_zip_bytes({"a.stl": STL, "b.stl": STL}), "bundle.zip")
        manifest = _manifest([("42:bundle", "bundle.zip")])

        assets = asyncio.run(
            inbox._download_resolved_asset(
                _resolved(manifest, "42:bundle", "bundle.zip")
            )
        )

        assert len({asset.result_key for asset in assets}) == 2

    def test_records_where_in_the_container_each_entry_came_from(
        self, downloads
    ) -> None:
        import asyncio

        downloads(_zip_bytes({"nested/a.stl": STL}), "bundle.zip")
        manifest = _manifest([("42:bundle", "bundle.zip")])

        assets = asyncio.run(
            inbox._download_resolved_asset(
                _resolved(manifest, "42:bundle", "bundle.zip")
            )
        )

        assert assets[0].container_entry_path == "nested/a.stl"

    def test_expands_a_zip_that_is_not_named_zip(self, downloads) -> None:
        import asyncio

        downloads(_zip_bytes({"a.stl": STL}), "bundle.bin")
        manifest = _manifest([("42:bundle", "bundle.bin")])

        assets = asyncio.run(
            inbox._download_resolved_asset(
                _resolved(manifest, "42:bundle", "bundle.bin")
            )
        )

        # Providers serve archives under all sorts of names; the content decides.
        assert assets[0].container_entry_path == "a.stl"

    def test_leaves_a_3mf_whole(self, downloads) -> None:
        import asyncio

        downloads(_zip_bytes({"3D/3dmodel.model": b"<xml/>"}), "widget.3mf")
        manifest = _manifest([("42:widget", "widget.3mf")])

        assets = asyncio.run(
            inbox._download_resolved_asset(
                _resolved(manifest, "42:widget", "widget.3mf")
            )
        )

        # A 3MF is a zip by construction and one model by intent; unpacking it
        # would turn one selection into a pile of XML.
        assert len(assets) == 1
        assert assets[0].result_key == "self"


class TestDownloadAssets:
    def test_stages_a_plain_file(self, downloads, monkeypatch) -> None:
        import asyncio

        downloads(STL, "cube.stl")

        async def no_resolution(_url: str) -> None:
            return None

        monkeypatch.setattr(inbox.import_resolvers, "resolve_page_url", no_resolution)

        staged = asyncio.run(inbox._download_assets("https://example.test/cube.stl"))

        assert [name for _path, name in staged] == ["cube.stl"]

    def test_expands_an_archive(self, downloads, monkeypatch) -> None:
        import asyncio

        downloads(_zip_bytes({"a.stl": STL, "b.stl": STL}), "bundle.zip")

        async def no_resolution(_url: str) -> None:
            return None

        monkeypatch.setattr(inbox.import_resolvers, "resolve_page_url", no_resolution)

        staged = asyncio.run(inbox._download_assets("https://example.test/bundle.zip"))

        assert len(staged) == 2

    def test_follows_a_page_url_to_its_download(self, downloads, monkeypatch) -> None:
        import asyncio

        downloads(STL, "cube.stl")
        asked: list[str] = []

        async def resolve(url: str) -> str:
            asked.append(url)
            return "https://cdn.example.test/real.stl"

        monkeypatch.setattr(inbox.import_resolvers, "resolve_page_url", resolve)

        asyncio.run(inbox._download_assets("https://www.printables.com/model/42"))

        # A user pastes the page they are looking at, not the file behind it.
        assert asked == ["https://www.printables.com/model/42"]


class TestStageLocalCaptureAssets:
    def test_copies_a_single_browser_file_into_disposable_staging(
        self, staging: Path, tmp_path: Path
    ) -> None:
        import asyncio

        source = tmp_path / "browser.stl"
        source.write_bytes(STL)
        manifest = _manifest([("42:cube", "cube.stl")])

        assets = asyncio.run(
            inbox._stage_local_capture_assets(source, manifest, ["42:cube"])
        )

        # A copy, not a move: the browser's own staging stays owned by the inbox
        # item until the import succeeds.
        assert len(assets) == 1
        assert assets[0].staged_path != source
        assert source.exists()

    def test_expands_a_captured_zip_into_its_declared_members(
        self, staging: Path, tmp_path: Path
    ) -> None:
        import asyncio

        source = tmp_path / "browser.zip"
        source.write_bytes(_zip_bytes({"a.stl": STL, "b.stl": STL}))
        manifest = _manifest([("a.stl", "a.stl"), ("b.stl", "b.stl")])

        assets = asyncio.run(
            inbox._stage_local_capture_assets(source, manifest, ["a.stl", "b.stl"])
        )

        assert {asset.container_entry_path for asset in assets} == {"a.stl", "b.stl"}

    def test_drops_a_zip_member_the_manifest_never_declared(
        self, staging: Path, tmp_path: Path
    ) -> None:
        import asyncio

        source = tmp_path / "browser.zip"
        source.write_bytes(_zip_bytes({"a.stl": STL, "surprise.stl": STL}))
        manifest = _manifest([("a.stl", "a.stl")])

        assets = asyncio.run(
            inbox._stage_local_capture_assets(source, manifest, ["a.stl"])
        )

        # The manifest is the contract; a member it does not name is not part of
        # this capture, whatever the archive happens to contain.
        assert [asset.container_entry_path for asset in assets] == ["a.stl"]

    def test_falls_back_to_every_file_when_the_selection_matches_none(
        self, staging: Path, tmp_path: Path
    ) -> None:
        import asyncio

        source = tmp_path / "browser.stl"
        source.write_bytes(STL)
        manifest = _manifest([("42:cube", "cube.stl")])

        assets = asyncio.run(
            inbox._stage_local_capture_assets(source, manifest, ["nothing-matches"])
        )

        # A stale selection should import the capture, not nothing at all.
        assert len(assets) == 1
