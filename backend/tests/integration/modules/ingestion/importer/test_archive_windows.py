"""Archive windows do not expand again until owned staging is released."""

from pathlib import Path

import pytest

from app.core.cancellation import OperationCancelled
from app.modules.ingestion import importer
from tests.factories.content import gcode, zip_bytes


class _WindowProbe:
    """Delegate real extraction while making one owned output unreleasable."""

    def __init__(self):
        self.extracted = []
        self.blocked = None
        self.extract = importer.extract_selected_archive_entries
        self.unlink = Path.unlink

    def extract_entry(self, archive, names, **kwargs):
        self.extracted.append(tuple(names))
        return self.extract(archive, names, **kwargs)

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
        monkeypatch.setattr(
            Path,
            "unlink",
            lambda path, *args, **kwargs: probe.release(path, *args, **kwargs),
        )

        try:
            with pytest.raises(
                importer.ImportError_, match="batch_window_release_failed"
            ):
                next(iterator)
        finally:
            iterator.close()

        assert probe.extracted == [("first.gcode",)]
        assert output.exists()
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
        monkeypatch.setattr(
            Path,
            "unlink",
            lambda path, *args, **kwargs: probe.release(path, *args, **kwargs),
        )
        cancelled = OperationCancelled("withdrawn while consuming window")

        with pytest.raises(OperationCancelled) as caught:
            iterator.throw(cancelled)

        assert caught.value is cancelled
        assert any(
            "batch_window_release_failed" in note for note in cancelled.__notes__
        )
        assert probe.extracted == [("first.gcode",)]
        assert output.exists()
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

        with pytest.raises(importer.WindowReleaseError) as caught:
            try:
                raise primary
            finally:
                importer.discard_staged_files((output,), strict=True)

        assert caught.value.__cause__ is primary
        assert output.read_bytes() == body
