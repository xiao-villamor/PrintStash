"""Archive windows do not expand again until owned staging is released."""

import os
from pathlib import Path

import pytest

from app.core.cancellation import OperationCancelled
from app.modules.ingestion import importer
from tests.factories.content import gcode, zip_bytes


class _WindowProbe:
    """Delegate real extraction while making one owned output unreleasable."""

    def __init__(self):
        self.extracted = []
        self.blocked: Path | None = None
        self.extract = importer.extract_selected_archive_entries
        self.unlink = Path.unlink
        self.os_unlink = os.unlink

    def extract_entry(self, archive, names, **kwargs):
        self.extracted.append(tuple(names))
        return self.extract(archive, names, **kwargs)

    def remove(self, path, *args, **kwargs):
        if self.blocked is not None and Path(path).name == self.blocked.name:
            raise OSError("owned window cannot be released")
        return self.os_unlink(path, *args, **kwargs)

    def release(self, path, *args, **kwargs):
        if path == self.blocked:
            raise OSError("owned window cannot be released")
        return self.unlink(path, *args, **kwargs)


class TestWindowRelease:
    def test_stops_before_extracting_when_window_release_fails(
        self, tmp_path, local_storage, monkeypatch
    ):
        source = tmp_path / "durable-source.zip"
        body = zip_bytes(
            {
                "first.gcode": gcode(marker="first-window"),
                "second.gcode": gcode(marker="second-window"),
            }
        )
        source.write_bytes(body)
        probe = _WindowProbe()
        monkeypatch.setattr(
            importer, "extract_selected_archive_entries", probe.extract_entry
        )
        iterator = importer.iter_archive_entries(
            source, ["first.gcode", "second.gcode"]
        )
        entry, (output, name) = next(iterator)
        assert entry.name == name == "first.gcode"
        assert output.read_bytes() == gcode(marker="first-window")
        probe.blocked = output
        monkeypatch.setattr(os, "unlink", probe.remove)

        try:
            with pytest.raises(
                importer.ImportError_, match="batch_window_release_failed"
            ):
                next(iterator)
        finally:
            iterator.close()

        assert probe.extracted == [("first.gcode",)]
        assert (
            output.parent.with_name(output.parent.name + ".retired") / output.name
        ).exists()
        assert source.read_bytes() == body

    def test_preserves_cancellation_when_window_release_fails(
        self, tmp_path, local_storage, monkeypatch
    ):
        source = tmp_path / "cancelled-source.zip"
        body = zip_bytes(
            {
                "first.gcode": gcode(marker="cancelled-window"),
                "second.gcode": gcode(marker="never-extracted"),
            }
        )
        source.write_bytes(body)
        probe = _WindowProbe()
        monkeypatch.setattr(
            importer, "extract_selected_archive_entries", probe.extract_entry
        )
        iterator = importer.iter_archive_entries(
            source, ["first.gcode", "second.gcode"]
        )
        _, (output, _) = next(iterator)
        probe.blocked = output
        monkeypatch.setattr(os, "unlink", probe.remove)
        cancelled = OperationCancelled("withdrawn while consuming window")

        with pytest.raises(OperationCancelled) as caught:
            iterator.throw(cancelled)

        assert caught.value is cancelled
        assert any(
            "batch_window_release_failed" in note for note in cancelled.__notes__
        )
        assert probe.extracted == [("first.gcode",)]
        assert (
            output.parent.with_name(output.parent.name + ".retired") / output.name
        ).exists()
        assert source.read_bytes() == body

    def test_refuses_to_unlink_a_replaced_window_output(
        self, tmp_path, local_storage, monkeypatch
    ):
        source = tmp_path / "replaced-source.zip"
        body = zip_bytes(
            {
                "first.gcode": gcode(marker="owned-window"),
                "second.gcode": gcode(marker="not-extracted"),
            }
        )
        source.write_bytes(body)
        probe = _WindowProbe()
        monkeypatch.setattr(
            importer, "extract_selected_archive_entries", probe.extract_entry
        )
        iterator = importer.iter_archive_entries(
            source, ["first.gcode", "second.gcode"]
        )
        _, (output, _) = next(iterator)
        owned = output.with_suffix(".owned")
        output.rename(owned)
        foreign = b"replacement owned by somebody else"
        output.write_bytes(foreign)

        with pytest.raises(
            importer.WindowReleaseError, match="batch_window_release_failed"
        ):
            next(iterator)

        assert probe.extracted == [("first.gcode",)]
        assert output.read_bytes() == foreign
        assert owned.read_bytes() == gcode(marker="owned-window")
        assert source.read_bytes() == body

    def test_stops_cleanup_when_primary_error_also_prevents_release(
        self, tmp_path, monkeypatch
    ):
        output = tmp_path / "failed-read.gcode"
        body = gcode(marker="failed-read-window")
        output.write_bytes(body)
        probe = _WindowProbe()
        probe.blocked = output
        monkeypatch.setattr(
            Path,
            "unlink",
            lambda path, *args, **kwargs: probe.release(path, *args, **kwargs),
        )
        primary = RuntimeError("source read failed")

        with pytest.raises(RuntimeError, match="source read failed") as caught:
            try:
                raise primary
            finally:
                importer.discard_staged_files((output,), strict=True)

        assert caught.value is primary
        assert any("batch_window_release_failed" in note for note in primary.__notes__)
        assert output.read_bytes() == body


class TestScratchDiscard:
    def test_defers_discard_until_live_window_closes(self, local_storage):
        from app.modules.ingestion import scratch_windows

        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=16
        ) as window:
            output = window.directory / "result.stl"
            output.write_bytes(b"owned")
            window.seal(output)
            importer.discard_staged_files((output,), strict=True)
            assert output.read_bytes() == b"owned"
        assert not output.exists()


class TestDownloadReplayAdmission:
    @pytest.mark.asyncio
    async def test_blocks_fetch_when_previous_epoch_cannot_release(
        self, local_storage, make_ingest_request, make_user, monkeypatch
    ):
        from app.db.models import IngestRequestKind, JobState
        from app.modules.ingestion import scratch_windows

        request = make_ingest_request(
            make_user(), kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )
        old = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD,
            max_bytes=16,
            owner=scratch_windows.JobWindowOwner(request.job_id, "old-epoch"),
        )
        output = old.directory / "partial"
        output.write_bytes(b"partial")
        old.seal(output)
        old.detach()
        unlink = os.unlink

        def refuse(path, *args, **kwargs):
            if Path(path).name == "partial":
                raise OSError("controlled unlink refusal")
            return unlink(path, *args, **kwargs)

        def forbidden_resolution(url):
            raise AssertionError("retry fetched before releasing previous bytes")

        monkeypatch.setattr(os, "unlink", refuse)
        monkeypatch.setattr(importer, "_resolve_or_raise", forbidden_resolution)
        with pytest.raises(scratch_windows.WindowCleanupError):
            await importer.download_to_staging(
                "https://example.test/new.stl",
                owner=scratch_windows.JobWindowOwner(request.job_id, "new-epoch"),
            )
        assert (
            old.directory.with_name(old.id + ".retired") / output.name
        ).read_bytes() == b"partial"
        monkeypatch.setattr(os, "unlink", unlink)
        assert scratch_windows.cleanup_window(old.id)
