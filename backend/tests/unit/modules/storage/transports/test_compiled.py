"""The image build fails when the wheel lacks a registered OpenDAL service.

The Dockerfile compiles only the services it names; a transport registered
without its cargo feature would ship as "not compiled" in every full image.
"""

from __future__ import annotations

import opendal
import pytest

from app.modules.storage.transports import compiled


class TestRegisteredServices:
    def test_lists_each_opendal_service_once(self) -> None:
        schemes = [service.scheme for service in compiled.registered_services()]

        assert schemes == ["s3", "webdav", "gdrive"]


class TestMain:
    def test_succeeds_when_every_service_constructs(self) -> None:
        assert compiled.main() == 0

    def test_names_a_service_the_wheel_lacks(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        build = opendal.Operator

        def without_gdrive(scheme: str, **options: str):
            if scheme == "gdrive":
                raise opendal.exceptions.Unsupported("scheme not compiled")
            return build(scheme, **options)

        monkeypatch.setattr(opendal, "Operator", without_gdrive)

        assert compiled.main() == 1
        assert "OpenDAL services not compiled: gdrive" in capsys.readouterr().err
