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

import asyncio
import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from printstash_core.imports import StagedAsset
from printstash_core.imports.contracts import CaptureManifestV2, ResolvedAsset
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import (
    CapacityReservation,
    File,
    InboxItem,
    InboxItemState,
    IngestionScratchWindow,
    JobKind,
    StagingLease,
)
from app.db.session import SessionFactory, get_session_factory
from app.modules.ingestion import inbox, scratch_windows
from tests.factories.ops import build_job_context

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
        async def download(
            _url: str,
            *,
            window_max_bytes: int | None = None,
            window: scratch_windows.ScratchWindow | None = None,
            owner: scratch_windows.WindowOwner | None = None,
            session_factory: SessionFactory | None = None,
        ) -> tuple[Path, str]:
            assert window is not None
            assert len(data) <= window.max_bytes
            assert window_max_bytes is None or len(data) <= window_max_bytes
            if owner is not None:
                assert isinstance(owner, scratch_windows.JobWindowOwner)
                with (
                    session_factory or get_session_factory()
                ).scoped_session() as session:
                    receipt = session.get(IngestionScratchWindow, window.id)
                    assert receipt is not None
                    assert (receipt.origin_job_id, receipt.execution_epoch) == (
                        owner.job_id,
                        owner.execution_epoch,
                    )
            path = window.directory / name
            path.write_bytes(data)
            window.seal(path)
            return path, name

        monkeypatch.setattr(inbox.importer, "download_to_staging", download)

    return serve


@dataclass(frozen=True)
class _Consumed:
    staged_path: Path
    bytes: bytes
    name: str
    result_key: str
    source_selection_id: str | None
    container_entry_path: str | None
    blob_sha256: str


@pytest.fixture
def batch_flow(db_session, make_user, make_inbox_item, make_job):
    """Observe each real commit, without retaining disposable staging windows."""

    def create(manifest=None):
        inbox.settings.incoming_dir.mkdir(parents=True, exist_ok=True)
        owner = make_user(superuser=True)
        item = make_inbox_item(
            owner,
            state=InboxItemState.IMPORTING,
            manifest=manifest.to_dict() if manifest else {"kind": "direct"},
            source_url="https://www.printables.com/model/42",
        )
        job = make_job(
            kind=JobKind.INGESTION_INBOX_IMPORT,
            subject=f"inbox_item/{item.id}",
            owner=owner,
        )
        item.job_id = job.id
        db_session.add(item)
        db_session.commit()
        context = {
            "collection_path": None,
            "tags": None,
            "source_url": item.source_url,
            "owner_id": owner.id,
        }
        flow = inbox._InboxBatchImport(
            item.id, context, get_session_factory(), build_job_context(job.id)
        )
        observed = []
        original = flow.batch._commit

        def consume(record, staged, source_url, member_title):
            if isinstance(staged, StagedAsset):
                snapshot = _Consumed(
                    staged.staged_path,
                    staged.staged_path.read_bytes(),
                    record.spec.display_name,
                    staged.result_key,
                    staged.source_selection_id,
                    staged.container_entry_path,
                    staged.blob_sha256,
                )
            else:
                path, name = staged
                data = path.read_bytes()
                snapshot = _Consumed(
                    path,
                    data,
                    name,
                    "self",
                    None,
                    None,
                    hashlib.sha256(data).hexdigest(),
                )
            result = original(record, staged, source_url, member_title)
            assert result is not None and "file_id" in result, result
            observed.append(snapshot)
            return result

        flow.batch._commit = consume
        return flow, observed

    return create


@pytest.fixture
def remote_assets(batch_flow):
    async def consume(resolved):
        flow, observed = batch_flow(resolved.manifest)
        await flow.remote(
            f"{resolved.source_item_id}:{resolved.source_selection_id}",
            resolved.source_selection_id,
            resolved.source_filename,
            resolved.download_url,
            resolved,
        )
        return observed

    return consume


@pytest.fixture
def local_assets(batch_flow):
    async def consume(source, manifest, selected):
        flow, observed = batch_flow(manifest)
        files = {file.id: file for file in manifest.files}
        wanted = [key for key in selected if key in files] or list(files)
        if source.suffix == ".zip":
            flow.archive(
                source,
                [files[key].name for key in wanted],
                manifest.source.canonical_url,
                manifest=manifest,
            )
        else:
            resolved = inbox._local_resolved_asset(manifest, wanted[0])
            spec = inbox.importer.direct_entry_spec(
                manifest.source.canonical_url, wanted[0], resolved.source_filename
            )
            flow.copy(source, spec, resolved)
        return observed

    return consume


class TestDownloadResolvedAsset:
    def test_stages_a_plain_file_as_one_asset(self, downloads, remote_assets) -> None:
        import asyncio

        downloads(STL, "cube.stl")
        manifest = _manifest([("42:cube", "cube.stl")])

        assets = asyncio.run(remote_assets(_resolved(manifest, "42:cube", "cube.stl")))

        assert len(assets) == 1
        assert assets[0].result_key == "self"
        assert assets[0].bytes == STL

    def test_hashes_what_it_staged(self, downloads, remote_assets) -> None:
        import asyncio

        downloads(STL, "cube.stl")
        manifest = _manifest([("42:cube", "cube.stl")])

        assets = asyncio.run(remote_assets(_resolved(manifest, "42:cube", "cube.stl")))

        # The hash is what a later dedupe and a provenance link are keyed on.
        assert len(assets[0].blob_sha256) == 64

    def test_expands_a_zip_into_one_asset_per_entry(
        self, downloads, remote_assets
    ) -> None:
        import asyncio

        downloads(_zip_bytes({"a.stl": STL, "b.stl": STL}), "bundle.zip")
        manifest = _manifest([("42:bundle", "bundle.zip")])

        assets = asyncio.run(
            remote_assets(_resolved(manifest, "42:bundle", "bundle.zip"))
        )

        assert len(assets) == 2

    def test_keeps_every_expanded_entry_pointing_at_its_selection(
        self, downloads, remote_assets
    ) -> None:
        import asyncio

        downloads(_zip_bytes({"a.stl": STL, "b.stl": STL}), "bundle.zip")
        manifest = _manifest([("42:bundle", "bundle.zip")])

        assets = asyncio.run(
            remote_assets(_resolved(manifest, "42:bundle", "bundle.zip"))
        )

        # Lose this and a partial retry cannot tell which of twenty entries failed.
        assert {asset.source_selection_id for asset in assets} == {"42:bundle"}

    def test_gives_each_expanded_entry_its_own_result_key(
        self, downloads, remote_assets
    ) -> None:
        import asyncio

        downloads(_zip_bytes({"a.stl": STL, "b.stl": STL}), "bundle.zip")
        manifest = _manifest([("42:bundle", "bundle.zip")])

        assets = asyncio.run(
            remote_assets(_resolved(manifest, "42:bundle", "bundle.zip"))
        )

        assert len({asset.result_key for asset in assets}) == 2

    def test_records_where_in_the_container_each_entry_came_from(
        self, downloads, remote_assets
    ) -> None:
        import asyncio

        downloads(_zip_bytes({"nested/a.stl": STL}), "bundle.zip")
        manifest = _manifest([("42:bundle", "bundle.zip")])

        assets = asyncio.run(
            remote_assets(_resolved(manifest, "42:bundle", "bundle.zip"))
        )

        assert assets[0].container_entry_path == "nested/a.stl"

    def test_expands_a_zip_that_is_not_named_zip(
        self, downloads, remote_assets
    ) -> None:
        import asyncio

        downloads(_zip_bytes({"a.stl": STL}), "bundle.bin")
        manifest = _manifest([("42:bundle", "bundle.bin")])

        assets = asyncio.run(
            remote_assets(_resolved(manifest, "42:bundle", "bundle.bin"))
        )

        # Providers serve archives under all sorts of names; the content decides.
        assert assets[0].container_entry_path == "a.stl"

    def test_leaves_a_3mf_whole(self, downloads, remote_assets) -> None:
        import asyncio

        downloads(_zip_bytes({"3D/3dmodel.model": b"<xml/>"}), "widget.3mf")
        manifest = _manifest([("42:widget", "widget.3mf")])

        assets = asyncio.run(
            remote_assets(_resolved(manifest, "42:widget", "widget.3mf"))
        )

        # A 3MF is a zip by construction and one model by intent; unpacking it
        # would turn one selection into a pile of XML.
        assert len(assets) == 1
        assert assets[0].result_key == "self"


class TestDownloadAssets:
    def test_stages_a_plain_file(self, downloads, batch_flow):
        downloads(STL, "cube.stl")
        flow, observed = batch_flow()
        asyncio.run(
            flow.remote(
                "https://example.test/cube.stl",
                "cube.stl",
                "cube.stl",
                "https://example.test/cube.stl",
            )
        )
        assert [asset.name for asset in observed] == ["cube.stl"]
        assert observed[0].bytes == STL

    def test_expands_an_archive(self, downloads, batch_flow):
        downloads(_zip_bytes({"a.stl": STL, "b.stl": STL}), "bundle.zip")
        flow, observed = batch_flow()
        asyncio.run(
            flow.remote(
                "https://example.test/bundle.zip",
                "bundle",
                "bundle.zip",
                "https://example.test/bundle.zip",
            )
        )
        assert len(observed) == 2
        assert {asset.name for asset in observed} == {"a.stl", "b.stl"}

    def test_follows_a_page_url_to_its_download(
        self, downloads, batch_flow, monkeypatch
    ):
        downloads(STL, "cube.stl")
        asked = []

        async def resolve(url):
            asked.append(url)
            return "https://cdn.example.test/real.stl"

        monkeypatch.setattr(inbox.import_resolvers, "resolve_page_url", resolve)
        flow, _ = batch_flow()
        context = dict(
            flow.context, manifest={"kind": "direct"}, selected=[], staging_key=None
        )
        asyncio.run(
            inbox._run_import(
                flow.item_id,
                context,
                flow.factory,
                job_context=build_job_context(flow.execution.job_id),
            )
        )
        assert asked == ["https://www.printables.com/model/42"]


class TestStageLocalCaptureAssets:
    def test_copies_a_single_browser_file_into_disposable_staging(
        self, staging: Path, tmp_path: Path, local_assets
    ) -> None:
        import asyncio

        source = tmp_path / "browser.stl"
        source.write_bytes(STL)
        manifest = _manifest([("42:cube", "cube.stl")])

        assets = asyncio.run(local_assets(source, manifest, ["42:cube"]))

        # A copy, not a move: the browser's own staging stays owned by the inbox
        # item until the import succeeds.
        assert len(assets) == 1
        assert assets[0].staged_path != source
        assert source.exists()

    def test_expands_a_captured_zip_into_its_declared_members(
        self, staging: Path, tmp_path: Path, local_assets
    ) -> None:
        import asyncio

        source = tmp_path / "browser.zip"
        source.write_bytes(_zip_bytes({"a.stl": STL, "b.stl": STL}))
        manifest = _manifest([("a.stl", "a.stl"), ("b.stl", "b.stl")])

        assets = asyncio.run(local_assets(source, manifest, ["a.stl", "b.stl"]))

        assert {asset.container_entry_path for asset in assets} == {"a.stl", "b.stl"}

    def test_drops_a_zip_member_the_manifest_never_declared(
        self, staging: Path, tmp_path: Path, local_assets
    ) -> None:
        import asyncio

        source = tmp_path / "browser.zip"
        source.write_bytes(_zip_bytes({"a.stl": STL, "surprise.stl": STL}))
        manifest = _manifest([("a.stl", "a.stl")])

        assets = asyncio.run(local_assets(source, manifest, ["a.stl"]))

        # The manifest is the contract; a member it does not name is not part of
        # this capture, whatever the archive happens to contain.
        assert [asset.container_entry_path for asset in assets] == ["a.stl"]

    def test_falls_back_to_every_file_when_the_selection_matches_none(
        self, staging: Path, tmp_path: Path, local_assets
    ) -> None:
        import asyncio

        source = tmp_path / "browser.stl"
        source.write_bytes(STL)
        manifest = _manifest([("42:cube", "cube.stl")])

        assets = asyncio.run(local_assets(source, manifest, ["nothing-matches"]))

        # A stale selection should import the capture, not nothing at all.
        assert len(assets) == 1

    def test_refuses_browser_copy_for_withdrawn_attempt(
        self, staging, tmp_path, local_assets
    ):
        import asyncio

        from app.core.cancellation import OperationCancelled, cancellation_scope

        source = tmp_path / "withdrawn-browser.stl"
        source.write_bytes(STL)
        manifest = _manifest([("42:cube", "cube.stl")])

        with cancellation_scope(lambda: True), pytest.raises(OperationCancelled):
            asyncio.run(local_assets(source, manifest, ["42:cube"]))

        assert source.read_bytes() == STL
        assert list(staging.iterdir()) == []

    def test_preserves_the_digest_of_a_browser_copy(
        self, staging, tmp_path, local_assets
    ):
        import asyncio
        import hashlib

        source = tmp_path / "hash-browser.stl"
        source.write_bytes(STL)
        manifest = _manifest([("42:cube", "cube.stl")])

        assets = asyncio.run(local_assets(source, manifest, ["42:cube"]))

        assert assets[0].blob_sha256 == hashlib.sha256(STL).hexdigest()
        assert assets[0].bytes == source.read_bytes() == STL


class TestCopyImportSource:
    def test_preserves_a_collided_destination(self, staging, tmp_path):
        source = tmp_path / "durable-browser.stl"
        source.write_bytes(STL)
        target = staging / "already-owned.stl"
        target.write_bytes(b"other copy")

        with pytest.raises(FileExistsError):
            inbox._copy_import_source(source, target)

        assert source.read_bytes() == STL
        assert target.read_bytes() == b"other copy"
        assert list(staging.iterdir()) == [target]


class TestStageCaptureUploadSlotAssets:
    def test_stops_slot_copying_between_entries(
        self,
        local_storage,
        db_session,
        make_user,
        monkeypatch,
    ):
        from io import BytesIO

        from app.core.cancellation import OperationCancelled, cancellation_scope
        from app.modules.storage.storage_backend.runtime import get_backend
        from app.schemas.inbox import CaptureUploadSlotsCreate
        from tests.factories.capture import capture_source

        owner = make_user(superuser=True)
        bodies = {"one": STL, "two": STL + b"second"}
        source_url = "https://www.printables.com/model/42"
        payload = CaptureUploadSlotsCreate.model_validate(
            {
                "source_url": source_url,
                "capture_source": capture_source(
                    provider="printables",
                    canonical_url=source_url,
                    source_item_id="42",
                ),
                "files": [
                    {
                        "id": file_id,
                        "filename": file_id + ".stl",
                        "media_type": "application/octet-stream",
                        "size_bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                    for file_id, body in bodies.items()
                ],
            }
        )
        item, slots = inbox.create_capture_upload_slots(db_session, owner, payload)
        for slot in slots:
            assert slot.source_file_id is not None
            inbox.upload_capture_slot(
                db_session,
                slot,
                stream=BytesIO(bodies[slot.source_file_id]),
                media_type="application/octet-stream",
            )
        inbox.finalize_capture_upload(db_session, owner, item.id)
        db_session.expire_all()
        item = db_session.get(InboxItem, item.id)
        assert item is not None
        job_id = inbox.begin_import(db_session, item, ["one", "two"])
        assert job_id is not None
        context = build_job_context(job_id)
        import_context = inbox._import_context(db_session, item, job_id)
        backend = get_backend()
        source_keys = {slot.source_file_id: slot.storage_key for slot in slots}
        baseline_file_ids = set(db_session.exec(select(File.id)).all())
        committed = []
        consume = inbox._InboxBatchImport.consume

        def commit_then_withdraw(flow, spec, staged, *args, **kwargs):
            result = consume(flow, spec, staged, *args, **kwargs)
            committed.append(spec.display_name)
            return result

        monkeypatch.setattr(inbox._InboxBatchImport, "consume", commit_then_withdraw)
        with (
            cancellation_scope(lambda: bool(committed)),
            pytest.raises(OperationCancelled),
        ):
            asyncio.run(
                inbox._run_import(
                    item.id,
                    import_context,
                    get_session_factory(),
                    job_context=context,
                )
            )

        assert committed == ["one.stl"]
        for file_id, key in source_keys.items():
            assert key is not None
            assert backend.read_bytes(key) == bodies[file_id]
        with get_session_factory().scoped_session() as session:
            files = [
                file
                for file in session.exec(select(File)).all()
                if file.id not in baseline_file_ids
            ]
            assert len(files) == 1
            assert backend.read_bytes(files[0].path) == bodies["one"]
            assert (
                len(
                    session.exec(
                        select(StagingLease).where(StagingLease.job_id == job_id)
                    ).all()
                )
                == 2
            )
            assert session.exec(select(IngestionScratchWindow)).all() == []
            assert session.exec(select(CapacityReservation)).all() == []
        root = inbox.settings.incoming_dir / "scratch-windows"
        assert not root.exists() or list(root.iterdir()) == []
