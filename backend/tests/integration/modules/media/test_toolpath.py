"""Toolpath text: ASCII read straight from the Artifact, binary converted by libbgcode.

Binary conversion is the ``derivatives.toolpath`` derivative's work: ``convert`` runs
the bounded official converter on a temporary copy, once per recipe, and never
touches the original. ``read_ascii`` serves ASCII G-code, which already is its
toolpath. If this goes red, a hostile or broken file can exhaust the host, a
partial conversion can be served as a preview, or the original bytes change.
"""

import hashlib
from pathlib import Path

import pytest

from app.core.config import _overlay
from app.core.errors import ErrorKind, OperationError
from app.db.models import FileType
from app.modules.media import toolpath
from app.modules.storage.storage_backend.runtime import get_backend
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


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    directory = tmp_path / "conversion"
    directory.mkdir()
    return directory


@pytest.fixture
def constrained_artifact(make_model, make_file, tmp_path):
    content = b"GCDE\x01\x00\x00\x00\x01\x00"
    artifact = make_file(
        make_model(),
        filename="limits.bgcode",
        path=f"limits/{tmp_path.name}/input.bgcode",
        size_bytes=len(content),
    )
    get_backend().write_bytes(content, artifact.path)
    return artifact


@pytest.mark.bgcode
class TestOfficialConversion:
    def test_matches_official_ascii_reference(self, binary_artifact, workdir):
        output = toolpath.convert(binary_artifact, workdir)

        assert (
            output.read_bytes()
            == (FIXTURES_DIR / "bgcode/prusaslicer.gcode").read_bytes()
        )

    def test_leaves_the_original_untouched(self, binary_artifact, workdir):
        original = get_backend().read_bytes(binary_artifact.path)

        toolpath.convert(binary_artifact, workdir)

        assert get_backend().read_bytes(binary_artifact.path) == original

    def test_rejects_a_truncated_container(self, binary_artifact, workdir):
        content = get_backend().read_bytes(binary_artifact.path)
        get_backend().direct_path(binary_artifact.path).write_bytes(content[:-13])

        with pytest.raises(OperationError) as error:
            toolpath.convert(binary_artifact, workdir)

        assert error.value.kind is ErrorKind.UNPROCESSABLE

    def test_rejects_a_checksum_mismatch(self, binary_artifact, workdir):
        content = bytearray(get_backend().read_bytes(binary_artifact.path))
        content[-1] ^= 1
        get_backend().direct_path(binary_artifact.path).write_bytes(bytes(content))

        with pytest.raises(OperationError) as error:
            toolpath.convert(binary_artifact, workdir)

        assert error.value.kind is ErrorKind.UNPROCESSABLE


class TestConversionLimits:
    def test_input_limit_is_checked_before_storage_read(
        self, constrained_artifact, workdir, monkeypatch
    ):
        _overlay["toolpath_input_max_mb"] = 1
        constrained_artifact.size_bytes = 2 * 1024 * 1024
        monkeypatch.setattr(
            toolpath, "resolve", lambda _file: pytest.fail("oversized Artifact opened")
        )

        with pytest.raises(OperationError) as error:
            toolpath.convert(constrained_artifact, workdir)

        assert error.value.kind is ErrorKind.TOO_LARGE

    def test_actual_input_bytes_obey_limit_despite_stale_size(
        self, constrained_artifact, workdir
    ):
        _overlay["toolpath_input_max_mb"] = 1
        get_backend().direct_path(constrained_artifact.path).write_bytes(
            b"X" * (1024 * 1024 + 1)
        )

        with pytest.raises(OperationError) as error:
            toolpath.convert(constrained_artifact, workdir)

        assert error.value.kind is ErrorKind.TOO_LARGE

    def test_output_limit_stops_the_child(
        self, constrained_artifact, workdir, tmp_path
    ):
        from tests.fakes.bgcode import converter_script

        _overlay["toolpath_output_max_mb"] = 1
        _overlay["bgcode_executable"] = str(
            converter_script(
                tmp_path,
                "source.with_suffix('.gcode').write_bytes(b'X' * (2 * 1024 * 1024))",
            )
        )

        with pytest.raises(OperationError) as error:
            toolpath.convert(constrained_artifact, workdir)

        assert error.value.kind is ErrorKind.TOO_LARGE

    def test_timeout_stops_the_child(self, constrained_artifact, workdir, tmp_path):
        from tests.fakes.bgcode import converter_script

        _overlay["toolpath_timeout_seconds"] = 1
        _overlay["bgcode_executable"] = str(
            converter_script(
                tmp_path,
                "source.with_suffix('.gcode').write_bytes(b'partial')\ntime.sleep(60)",
            )
        )

        with pytest.raises(OperationError) as error:
            toolpath.convert(constrained_artifact, workdir)

        assert error.value.kind is ErrorKind.TIMEOUT

    def test_address_space_limit_prevents_unbounded_allocation(
        self, constrained_artifact, workdir, tmp_path
    ):
        from tests.fakes.bgcode import converter_script

        _overlay["toolpath_memory_max_mb"] = 64
        _overlay["bgcode_executable"] = str(
            converter_script(
                tmp_path,
                "content = bytearray(128 * 1024 * 1024)\n"
                "source.with_suffix('.gcode').write_bytes(b'unlimited')",
            )
        )

        with pytest.raises(OperationError) as error:
            toolpath.convert(constrained_artifact, workdir)

        assert error.value.kind is ErrorKind.UNPROCESSABLE


@pytest.mark.bgcode
class TestDocumentedCodecs:
    @pytest.mark.parametrize("compression", [0, 1, 2, 3])
    @pytest.mark.parametrize("encoding", [0, 1, 2])
    def test_all_documented_gcode_codecs_preserve_commands(
        self,
        compression,
        encoding,
        bgcode_binary,
        tmp_path,
        workdir,
        make_model,
        make_file,
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

        converted = toolpath.convert(artifact, workdir).read_bytes()

        def commands(content):
            return [
                re.sub(rb"\s+", b"", line.split(b";", 1)[0]).upper()
                for line in content.splitlines()
                if line.strip() and not line.lstrip().startswith(b";")
            ]

        assert commands(converted) == commands(original)


class TestConversionValidation:
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

    def test_rejects_non_gcode_artifacts(self, make_model, make_file, workdir):
        artifact = make_file(make_model(), file_type=FileType.STL)

        with pytest.raises(OperationError) as error:
            toolpath.convert(artifact, workdir)

        assert error.value.kind is ErrorKind.NOT_FOUND

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(b"plain text", id="text-named-bgcode"),
            pytest.param(b"GCDE", id="short-header"),
            pytest.param(b"GCDE\x02\x00\x00\x00\x01\x00", id="unsupported-version"),
        ],
    )
    def test_refuses_invalid_binary_headers(
        self, constrained_artifact, content, workdir
    ):
        get_backend().direct_path(constrained_artifact.path).write_bytes(content)

        with pytest.raises(OperationError) as error:
            toolpath.convert(constrained_artifact, workdir)

        assert error.value.kind is ErrorKind.UNPROCESSABLE

    def test_missing_converter_is_actionable(self, constrained_artifact, workdir):
        _overlay["bgcode_executable"] = "/nonexistent/printstash-bgcode"

        with pytest.raises(OperationError) as error:
            toolpath.convert(constrained_artifact, workdir)

        assert (error.value.kind, error.value.detail) == (
            ErrorKind.UNAVAILABLE,
            "toolpath_converter_unavailable",
        )

    def test_missing_original_is_reported_gone(self, constrained_artifact, workdir):
        get_backend().direct_path(constrained_artifact.path).unlink()

        with pytest.raises(OperationError) as error:
            toolpath.convert(constrained_artifact, workdir)

        assert error.value.kind is ErrorKind.GONE


class TestReadAscii:
    def test_refuses_to_serve_archive_bytes_as_text(self, stored):
        from tests.fixtures.three_mf_projects import build_sliced_project

        artifact = stored("project.gcode.3mf", build_sliced_project())
        with pytest.raises(OperationError, match="plate_selection_required"):
            toolpath.read_ascii(artifact)

    def test_serves_the_artifact_bytes_as_its_toolpath(self, make_model, make_file):
        content = b"G90\nG1 X10 E1\n"
        artifact = make_file(
            make_model(), filename="plain.gcode", size_bytes=len(content)
        )
        get_backend().write_bytes(content, artifact.path)

        assert toolpath.read_ascii(artifact) == content

    def test_serves_a_verified_library_source_unchanged(
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

        assert toolpath.read_ascii(artifact) == content
        assert path.read_bytes() == content

    def test_output_obeys_the_browser_size_bound(self, constrained_artifact):
        _overlay["toolpath_output_max_mb"] = 1
        constrained_artifact.original_filename = "large.gcode"
        get_backend().direct_path(constrained_artifact.path).write_bytes(
            b";" * (1024 * 1024 + 1)
        )

        with pytest.raises(OperationError) as error:
            toolpath.read_ascii(constrained_artifact)

        assert error.value.kind is ErrorKind.TOO_LARGE

    def test_refuses_binary_content_behind_an_ascii_name(self, constrained_artifact):
        constrained_artifact.original_filename = "disguised.gcode"

        with pytest.raises(OperationError) as error:
            toolpath.read_ascii(constrained_artifact)

        assert error.value.detail == "toolpath_requires_conversion"

    def test_rejects_non_gcode_artifacts(self, make_model, make_file):
        artifact = make_file(make_model(), file_type=FileType.STL)

        with pytest.raises(OperationError) as error:
            toolpath.read_ascii(artifact)

        assert error.value.kind is ErrorKind.NOT_FOUND

    def test_missing_original_is_reported_gone(self, constrained_artifact):
        constrained_artifact.original_filename = "gone.gcode"
        get_backend().direct_path(constrained_artifact.path).unlink()

        with pytest.raises(OperationError) as error:
            toolpath.read_ascii(constrained_artifact)

        assert error.value.kind is ErrorKind.GONE
