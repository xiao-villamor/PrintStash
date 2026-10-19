"""Warm template request limits fail before any worker is forked."""

from io import StringIO
from unittest.mock import Mock

import pytest

from app.modules.media import worker_template as template


class TestTemplateInput:
    @pytest.mark.parametrize("size", [0, template.MAX_REQUEST_BYTES + 1])
    def test_refuses_invalid_request_size(self, size):
        connection = Mock()
        connection.recv.return_value = template.LENGTH.pack(size)
        with pytest.raises(ValueError, match="invalid worker request size"):
            template._request(connection)

    def test_refuses_truncated_request(self):
        connection = Mock()
        connection.recv.side_effect = [b"x", b""]
        with pytest.raises(ValueError, match="worker request ended early"):
            template._request(connection)

    @pytest.mark.parametrize("found", [False, True])
    def test_requires_virtual_size_evidence(self, monkeypatch, found):
        import builtins

        monkeypatch.setattr(
            builtins,
            "open",
            lambda *args: StringIO(
                "Name: worker\n" + ("VmSize: 123 kB\n" if found else "")
            ),
        )
        if found:
            assert template._virtual_size() == 123 * 1024
        else:
            with pytest.raises(RuntimeError, match="cannot read its virtual size"):
                template._virtual_size()

    def test_forwards_the_explicit_control_descriptor(self, monkeypatch):
        serve = Mock(return_value=7)
        monkeypatch.setattr(template, "serve", serve)
        assert template.main(["99"]) == 7
        serve.assert_called_once_with(99)

    @pytest.mark.parametrize("refused", [False, True])
    def test_does_not_serve_without_parent_death_protection(self, monkeypatch, refused):
        import ctypes
        from types import SimpleNamespace

        monkeypatch.setattr(template.sys, "platform", "linux")
        monkeypatch.setattr(
            ctypes,
            "CDLL",
            lambda *args, **kwargs: SimpleNamespace(prctl=lambda *args: int(refused)),
        )
        monkeypatch.setattr(template.os, "getppid", lambda: 1)
        if refused:
            with pytest.raises(OSError, match="PR_SET_PDEATHSIG"):
                template.serve(99)
        else:
            assert template.serve(99) == 0
