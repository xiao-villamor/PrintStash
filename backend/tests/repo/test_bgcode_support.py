"""The official-converter launcher retains build diagnostics on CI failures.

Build errors must expose their captured output while preserving the exception
for the caller; successful builds return the exported executable location.
"""

import subprocess

import pytest

from tests.bgcode_support import build_converter


class TestBuildConverter:
    def test_returns_exported_converter_path(self, tmp_path, monkeypatch):
        def successful_build(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout="built")

        monkeypatch.setattr(subprocess, "run", successful_build)

        assert build_converter(tmp_path) == tmp_path / "bgcode"

    @pytest.mark.parametrize("output", ["native build failure details", None])
    def test_exposes_original_build_failure(
        self, tmp_path, monkeypatch, capsys, output
    ):
        failure = subprocess.CalledProcessError(1, ["docker", "buildx"], output=output)

        def failed_build(*args, **kwargs):
            raise failure

        monkeypatch.setattr(subprocess, "run", failed_build)

        with pytest.raises(subprocess.CalledProcessError) as caught:
            build_converter(tmp_path)

        assert caught.value is failure
        assert capsys.readouterr().err == (output if output is not None else "")
