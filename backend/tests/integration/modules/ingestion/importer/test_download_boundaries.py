"""Download windows preserve bounded ownership through HTTP and source failures."""

from pathlib import Path

import httpx
import pytest
from sqlmodel import select

from app.core.config import settings
from app.core.url_safety import PinnedTarget
from app.db.models import CapacityReservation, IngestionScratchWindow
from app.modules.ingestion import importer, scratch_windows
from tests.factories.content import gcode, zip_bytes


@pytest.fixture
def download_transport(monkeypatch):
    """Stand in only for public DNS and outbound HTTP; keep real stream/window code."""
    state = {"response": None, "requests": []}

    def handle(request):
        state["requests"].append(str(request.url))
        response = state["response"]
        assert response is not None
        return response(request)

    monkeypatch.setattr(
        importer,
        "_resolve_or_raise",
        lambda url: PinnedTarget(url, "example.com", 443, "93.184.216.34"),
    )
    monkeypatch.setattr(
        importer, "pinned_transport", lambda _: httpx.MockTransport(handle)
    )
    return state


class TestDownloadBoundaries:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("url", "header", "expected"),
        [
            pytest.param(
                "https://example.com/source.stl",
                'attachment; filename="part;one.stl"; size=10',
                "part;one.stl",
                id="quoted-semicolon",
            ),
            pytest.param(
                "https://example.com/source.stl",
                "attachment; filename=part.stl; size=10",
                "part.stl",
                id="unquoted-parameter",
            ),
            pytest.param(
                "https://example.com/source.stl",
                'attachment; filename="%2Funsafe%2Fpart.stl"',
                "part.stl",
                id="encoded-path",
            ),
            pytest.param(
                "https://example.com/fallback.gcode",
                'attachment; filename=""',
                "fallback.gcode",
                id="empty-header-name",
            ),
            pytest.param(
                "https://example.com/part%20one.stl",
                "inline",
                "part one.stl",
                id="url-decoding",
            ),
            pytest.param(
                "https://example.com/", "inline", "download", id="empty-url-path"
            ),
            pytest.param(
                "https://example.com/part", "inline", "part", id="extensionless"
            ),
        ],
    )
    async def test_preserves_safe_download_names(
        self, local_storage, download_transport, url, header, expected
    ):
        body = gcode(marker="download-boundary")
        download_transport["response"] = lambda _: httpx.Response(
            200, headers={"content-disposition": header}, content=body
        )
        path, name = await importer.download_to_staging(url)
        try:
            assert name == expected
            assert path.suffix == (Path(expected).suffix or ".bin")
            assert path.read_bytes() == body
            assert download_transport["requests"] == [url]
        finally:
            importer.discard_staged_files((path,), strict=True)
        assert not path.exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "limit",
        [True, 0, -1, 1.5, "8"],
        ids=["boolean", "zero", "negative", "float", "string"],
    )
    async def test_refuses_invalid_window_limit_before_fetch(
        self, local_storage, download_transport, limit
    ):
        with pytest.raises(ValueError, match="invalid_batch_window_bytes"):
            await importer.download_to_staging(
                "https://example.com/part.stl", window_max_bytes=limit
            )
        assert download_transport["requests"] == []
        root = settings.incoming_dir / "scratch-windows"
        assert not root.exists() or list(root.iterdir()) == []

    @pytest.mark.asyncio
    async def test_refuses_redirect_without_location(
        self, local_storage, download_transport
    ):
        download_transport["response"] = lambda _: httpx.Response(302)
        with pytest.raises(
            importer.ImportError_, match="url_redirect_without_location"
        ):
            await importer.download_to_staging("https://example.com/part.stl")
        assert download_transport["requests"] == ["https://example.com/part.stl"]
        root = settings.incoming_dir / "scratch-windows"
        assert not root.exists() or list(root.iterdir()) == []

    @pytest.mark.asyncio
    async def test_stops_at_configured_redirect_ceiling(
        self, local_storage, download_transport
    ):
        def redirect(request):
            index = len(download_transport["requests"])
            return httpx.Response(302, headers={"location": f"/hop/{index}"})

        download_transport["response"] = redirect
        with pytest.raises(importer.ImportError_, match="url_too_many_redirects"):
            await importer.download_to_staging("https://example.com/start")
        assert download_transport["requests"] == ["https://example.com/start"] + [
            f"https://example.com/hop/{n}"
            for n in range(1, settings.url_import_max_redirects + 1)
        ]
        root = settings.incoming_dir / "scratch-windows"
        assert not root.exists() or list(root.iterdir()) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error", [OSError, PermissionError], ids=["oserror", "permissionerror"]
    )
    async def test_stream_failure_releases_owned_window(
        self, local_storage, db_session, download_transport, error
    ):
        fault = error("outbound stream refused")
        closed = []
        before_windows = {r.id for r in db_session.exec(select(IngestionScratchWindow))}
        before_credits = {
            r.operation_id for r in db_session.exec(select(CapacityReservation))
        }

        class FailedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"x" * (1024 * 1024)
                raise fault

            async def aclose(self):
                closed.append(True)

        download_transport["response"] = lambda _: httpx.Response(
            200, stream=FailedStream()
        )
        with pytest.raises(error) as raised:
            await importer.download_to_staging("https://example.com/part.stl")
        assert raised.value is fault
        assert closed == [True]
        assert {
            r.id for r in db_session.exec(select(IngestionScratchWindow))
        } == before_windows
        assert {
            r.operation_id for r in db_session.exec(select(CapacityReservation))
        } == before_credits
        root = settings.incoming_dir / "scratch-windows"
        assert not root.exists() or list(root.iterdir()) == []

    @pytest.mark.asyncio
    async def test_respects_caller_window_capacity(
        self, local_storage, download_transport
    ):
        download_transport["response"] = lambda _: httpx.Response(
            200, content=b"123456789"
        )
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.DOWNLOAD, max_bytes=8
        ) as window:
            directory = window.directory
            with pytest.raises(importer.ImportError_, match="batch_entry_too_large"):
                await importer.download_to_staging(
                    "https://example.com/part.stl", window=window
                )
            assert directory.exists()
            outputs = list(directory.glob(".printstash-url-*"))
            assert len(outputs) == 1
            assert outputs[0].read_bytes() == b""
            assert list(directory.glob("*.stl")) == []
        assert not directory.exists()


class TestArchiveReviewBoundaries:
    def test_crc_failure_preserves_archive_source(self, tmp_path):
        import zipfile

        source = tmp_path / "corrupted.zip"
        body = zip_bytes({"part.gcode": gcode(marker="crc-boundary")}, compress=False)
        source.write_bytes(body)
        with zipfile.ZipFile(source) as archive:
            info = archive.getinfo("part.gcode")
        corrupted = bytearray(body)
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        corrupted[offset] ^= 1
        source.write_bytes(corrupted)
        progress = []
        with pytest.raises(importer.ImportError_, match="^archive_invalid$"):
            importer.prepare_archive_for_review(
                source,
                on_chunk=lambda: None,
                on_entry=lambda *args: progress.append(args),
            )
        assert source.read_bytes() == corrupted
        assert progress == []
