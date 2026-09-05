"""Actual libbgcode conversion checks its structure, codecs and original bytes."""

import hashlib

import pytest

from app.core.config import _overlay
from app.services import toolpath
from app.services.storage_backend import get_backend
from tests.paths import FIXTURES_DIR


@pytest.fixture
def binary_artifact(make_model, make_file, bgcode_binary, tmp_path):
    _overlay["bgcode_executable"] = str(bgcode_binary)
    content = (FIXTURES_DIR / "bgcode/prusaslicer.bgcode").read_bytes()
    artifact = make_file(
        make_model(),
        filename="prusaslicer.bgcode",
        path=f"toolpath/{tmp_path.name}/original.bgcode",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    get_backend().write_bytes(content, artifact.path)
    return artifact


@pytest.mark.bgcode
class TestOfficialToolpath:
    @pytest.mark.asyncio
    async def test_matches_official_ascii_reference(self, binary_artifact):
        original = get_backend().read_bytes(binary_artifact.path)
        result = await toolpath.render(binary_artifact)
        assert result == (FIXTURES_DIR / "bgcode/prusaslicer.gcode").read_bytes()
        assert get_backend().read_bytes(binary_artifact.path) == original

    @pytest.mark.asyncio
    async def test_rejects_a_truncated_container(self, binary_artifact):
        from fastapi import HTTPException

        content = get_backend().read_bytes(binary_artifact.path)
        get_backend().direct_path(binary_artifact.path).write_bytes(content[:-13])
        with pytest.raises(HTTPException) as error:
            await toolpath.render(binary_artifact)
        assert error.value.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_a_checksum_mismatch(self, binary_artifact):
        from fastapi import HTTPException

        content = bytearray(get_backend().read_bytes(binary_artifact.path))
        content[-1] ^= 1
        get_backend().direct_path(binary_artifact.path).write_bytes(bytes(content))
        with pytest.raises(HTTPException) as error:
            await toolpath.render(binary_artifact)
        assert error.value.status_code == 422


@pytest.fixture
def constrained_artifact(make_model, make_file, tmp_path, monkeypatch):
    content = b"GCDE\x01\x00\x00\x00\x01\x00"
    artifact = make_file(
        make_model(),
        filename="limits.bgcode",
        path=f"limits/{tmp_path.name}/input.bgcode",
        size_bytes=len(content),
    )
    get_backend().write_bytes(content, artifact.path)
    temporary_directory = toolpath.tempfile.TemporaryDirectory
    monkeypatch.setattr(
        toolpath.tempfile,
        "TemporaryDirectory",
        lambda **kwargs: temporary_directory(dir=tmp_path, **kwargs),
    )
    return artifact


class TestToolpathLimits:
    @pytest.mark.asyncio
    async def test_input_limit_is_checked_before_storage_read(
        self, constrained_artifact, monkeypatch
    ):
        from fastapi import HTTPException

        _overlay["toolpath_input_max_mb"] = 1
        constrained_artifact.size_bytes = 2 * 1024 * 1024
        monkeypatch.setattr(
            toolpath, "resolve", lambda _file: pytest.fail("oversized Artifact opened")
        )
        with pytest.raises(HTTPException) as error:
            await toolpath.render(constrained_artifact)
        assert error.value.status_code == 413

    @pytest.mark.asyncio
    async def test_output_limit_stops_the_child(self, constrained_artifact, tmp_path):
        from fastapi import HTTPException

        from tests.fakes.bgcode import converter_script

        _overlay["toolpath_output_max_mb"] = 1
        _overlay["bgcode_executable"] = str(
            converter_script(
                tmp_path,
                "source.with_suffix('.gcode').write_bytes(b'X' * (2 * 1024 * 1024))",
            )
        )
        with pytest.raises(HTTPException) as error:
            await toolpath.render(constrained_artifact)
        assert error.value.status_code == 413
        assert list(tmp_path.glob("printstash-toolpath-*")) == []

    @pytest.mark.asyncio
    async def test_timeout_cleans_conversion_resources(
        self, constrained_artifact, tmp_path
    ):
        from fastapi import HTTPException

        from tests.fakes.bgcode import converter_script

        _overlay["toolpath_timeout_seconds"] = 1
        _overlay["bgcode_executable"] = str(
            converter_script(
                tmp_path,
                "source.with_suffix('.gcode').write_bytes(b'partial')\ntime.sleep(60)",
            )
        )
        with pytest.raises(HTTPException) as error:
            await toolpath.render(constrained_artifact)
        assert error.value.status_code == 504
        assert list(tmp_path.glob("printstash-toolpath-*")) == []

    @pytest.mark.asyncio
    async def test_cancel_cleans_conversion_resources(
        self, constrained_artifact, tmp_path
    ):
        import asyncio

        from tests.fakes.bgcode import converter_script

        _overlay["bgcode_executable"] = str(
            converter_script(
                tmp_path,
                "source.with_suffix('.gcode').write_bytes(b'partial')\ntime.sleep(60)",
            )
        )
        task = asyncio.create_task(toolpath.render(constrained_artifact))
        try:
            async with asyncio.timeout(10):
                while not list(tmp_path.glob("printstash-toolpath-*/input.gcode")):
                    await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert list(tmp_path.glob("printstash-toolpath-*")) == []
            assert toolpath._active == 0
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_busy_converter_does_not_start_another_process(
        self, constrained_artifact, monkeypatch
    ):
        from fastapi import HTTPException

        monkeypatch.setattr(toolpath, "_active", 2)
        monkeypatch.setattr(
            toolpath,
            "resolve",
            lambda _file: pytest.fail("busy converter read storage"),
        )
        with pytest.raises(HTTPException) as error:
            await toolpath.render(constrained_artifact)
        assert error.value.status_code == 429
        assert error.value.headers["Retry-After"] == "2"

    @pytest.mark.asyncio
    async def test_address_space_limit_prevents_unbounded_allocation(
        self, constrained_artifact, tmp_path
    ):
        from fastapi import HTTPException

        from tests.fakes.bgcode import converter_script

        _overlay["toolpath_memory_max_mb"] = 64
        _overlay["bgcode_executable"] = str(
            converter_script(
                tmp_path,
                "content = bytearray(128 * 1024 * 1024)\nsource.with_suffix('.gcode').write_bytes(b'unlimited')",
            )
        )
        with pytest.raises(HTTPException) as error:
            await toolpath.render(constrained_artifact)
        assert error.value.status_code == 422
        assert list(tmp_path.glob("printstash-toolpath-*")) == []

    @pytest.mark.asyncio
    async def test_cancel_during_storage_copy_waits_for_writer_cleanup(
        self, constrained_artifact, tmp_path, monkeypatch
    ):
        import asyncio
        import threading

        started = threading.Event()
        release = threading.Event()
        original_copy = toolpath._copy_input

        def delayed_copy(file, target):
            started.set()
            if not release.wait(timeout=10):
                raise RuntimeError("writer not released")
            original_copy(file, target)

        monkeypatch.setattr(toolpath, "_copy_input", delayed_copy)
        task = asyncio.create_task(toolpath.render(constrained_artifact))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert list(tmp_path.glob("printstash-toolpath-*")) == []
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.bgcode
class TestDocumentedCodecs:
    @pytest.mark.parametrize("compression", [0, 1, 2, 3])
    @pytest.mark.parametrize("encoding", [0, 1, 2])
    @pytest.mark.asyncio
    async def test_all_documented_gcode_codecs_preserve_commands(
        self, compression, encoding, bgcode_binary, tmp_path, make_model, make_file
    ):
        import re
        import subprocess

        original = (FIXTURES_DIR / "bgcode/prusaslicer.gcode").read_bytes()
        source = tmp_path / "codec.gcode"
        source.write_bytes(original)
        subprocess.run(
            [
                str(bgcode_binary),
                str(source),
                "--checksum=1",
                f"--gcode_compression={compression}",
                f"--gcode_encoding={encoding}",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        encoded = source.with_suffix(".bgcode").read_bytes()
        artifact = make_file(
            make_model(),
            filename="codec.bgcode",
            path=f"codecs/{tmp_path.name}/codec.bgcode",
            size_bytes=len(encoded),
        )
        get_backend().write_bytes(encoded, artifact.path)
        _overlay["bgcode_executable"] = str(bgcode_binary)
        converted = await toolpath.render(artifact)

        def commands(content):
            return [
                re.sub(rb"\s+", b"", line.split(b";", 1)[0]).upper()
                for line in content.splitlines()
                if line.strip() and not line.lstrip().startswith(b";")
            ]

        assert commands(converted) == commands(original)


class TestToolpathValidation:
    def test_failed_destination_never_acquires_source_resources(
        self, constrained_artifact, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            toolpath,
            "resolve",
            lambda _file: pytest.fail("source acquired before destination"),
        )
        with pytest.raises(FileNotFoundError):
            toolpath._copy_input(
                constrained_artifact, tmp_path / "absent" / "input.bgcode"
            )

    @pytest.mark.asyncio
    async def test_rejects_non_gcode_artifacts(self, make_model, make_file):
        from fastapi import HTTPException

        from app.db.models import FileType

        artifact = make_file(make_model(), file_type=FileType.STL)
        with pytest.raises(HTTPException) as error:
            await toolpath.render(artifact)
        assert error.value.status_code == 404

    @pytest.mark.parametrize(
        "content", [b"plain text", b"GCDE", b"GCDE\x02\x00\x00\x00\x01\x00"]
    )
    @pytest.mark.asyncio
    async def test_refuses_invalid_binary_headers(
        self, constrained_artifact, content, tmp_path
    ):
        from fastapi import HTTPException

        get_backend().direct_path(constrained_artifact.path).write_bytes(content)
        with pytest.raises(HTTPException) as error:
            await toolpath.render(constrained_artifact)
        assert error.value.status_code == 422
        assert list(tmp_path.glob("printstash-toolpath-*")) == []

    @pytest.mark.asyncio
    async def test_missing_converter_is_actionable(self, constrained_artifact):
        from fastapi import HTTPException

        _overlay["bgcode_executable"] = "/nonexistent/printstash-bgcode"
        with pytest.raises(HTTPException) as error:
            await toolpath.render(constrained_artifact)
        assert error.value.status_code == 503
        assert error.value.detail == "toolpath_converter_unavailable"

    @pytest.mark.asyncio
    async def test_missing_original_does_not_produce_partial_preview(
        self, constrained_artifact
    ):
        from fastapi import HTTPException

        get_backend().direct_path(constrained_artifact.path).unlink()
        with pytest.raises(HTTPException) as error:
            await toolpath.render(constrained_artifact)
        assert error.value.status_code == 410

    @pytest.mark.asyncio
    async def test_actual_input_bytes_obey_limit_despite_stale_size(
        self, constrained_artifact, tmp_path
    ):
        from fastapi import HTTPException

        _overlay["toolpath_input_max_mb"] = 1
        get_backend().direct_path(constrained_artifact.path).write_bytes(
            b"X" * (1024 * 1024 + 1)
        )
        with pytest.raises(HTTPException) as error:
            await toolpath.render(constrained_artifact)
        assert error.value.status_code == 413
        assert list(tmp_path.glob("printstash-toolpath-*")) == []

    @pytest.mark.asyncio
    async def test_ascii_output_obeys_browser_size_bound(self, constrained_artifact):
        from fastapi import HTTPException

        _overlay["toolpath_output_max_mb"] = 1
        constrained_artifact.original_filename = "large.gcode"
        get_backend().direct_path(constrained_artifact.path).write_bytes(
            b";" * (1024 * 1024 + 1)
        )
        with pytest.raises(HTTPException) as error:
            await toolpath.render(constrained_artifact)
        assert error.value.status_code == 413

    @pytest.mark.asyncio
    async def test_verified_library_source_has_the_same_toolpath(
        self, make_model, make_file, tmp_path
    ):
        content = b"G90\nG1 X10 E1\n"
        path = tmp_path / "library.gcode"
        path.write_bytes(content)
        artifact = make_file(
            make_model(),
            path=str(path),
            filename=path.name,
            external=True,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        assert await toolpath.render(artifact) == content
        assert path.read_bytes() == content
