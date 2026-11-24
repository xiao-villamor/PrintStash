"""Native plugin failures must never cancel Orca's G-code export.

The external Orca binding is represented by the documented 2.5 contract.
Failures are injected only at the HTTP boundary; parsing and multipart are real.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from tests.fakes.orca_host import Step, export_context, plugin_host


@pytest.fixture
def host():
    with plugin_host() as loaded:
        yield loaded


@pytest.fixture
def configured(host):
    capability = host.capabilities[0]()
    capability.save_config(
        json.dumps(
            {
                "enabled": True,
                "url": "https://printstash.example.test",
                "username": "test-user",
                "api_key": "psk_fake_test_key",
            }
        )
    )
    return capability


@pytest.fixture
def exported(tmp_path):
    path = tmp_path / "export.gcode.pp"
    path.write_text("; printing object bracket.stl id:0 copy 0\nG28\n")
    return export_context(path)


class TestPrintStashPlugin:
    def test_registers_one_native_capability(self, host):
        assert [cap().get_name() for cap in host.capabilities] == [
            "Upload to PrintStash"
        ]


class TestParseConfig:
    def test_normalizes_saved_settings(self, host):
        config = host.module.parse_config(
            json.dumps(
                {
                    "enabled": True,
                    "url": "https://example.test/stash/",
                    "username": " user ",
                    "api_key": "psk_fake_key",
                    "collection": "Parts/Useful",
                    "revision_notes": "Works well",
                    "revision_status": "known_good",
                    "recommended": True,
                }
            )
        )

        assert config == {
            "enabled": True,
            "url": "https://example.test/stash",
            "username": "user",
            "api_key": "psk_fake_key",
            "collection": "Parts/Useful",
            "strict_mapping": False,
            "revision_label": None,
            "revision_notes": "Works well",
            "revision_status": "known_good",
            "recommended": True,
        }

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("null", id="null"),
            pytest.param("[]", id="array"),
            pytest.param("broken", id="malformed-json"),
            pytest.param('{"enabled":"false"}', id="boolean-string"),
            pytest.param('{"api_key":7}', id="wrong-text-type"),
            pytest.param('{"revision_status":"invalid"}', id="unknown-status"),
            pytest.param('{"enabled":true}', id="missing-credentials"),
            pytest.param('{"unexpected":true}', id="unknown-setting"),
        ],
    )
    def test_rejects_malformed_settings(self, host, raw):
        with pytest.raises((ValueError, TypeError)):
            host.module.parse_config(raw)

    @pytest.mark.parametrize(
        "url",
        [
            "file:///tmp/stash",
            "https://user:secret@example.test",
            "https://example.test?api_key=secret",
            "https://example.test/#secret",
            "https://",
            "http://example.test:99999",
            "http://example.test:invalid",
        ],
        ids=[
            "scheme",
            "userinfo",
            "query",
            "fragment",
            "missing-host",
            "port-range",
            "port-text",
        ],
    )
    def test_rejects_invalid_server_urls(self, host, url):
        with pytest.raises((ValueError, TypeError)):
            host.module.parse_config(
                json.dumps(
                    {
                        "enabled": True,
                        "url": url,
                        "username": "user",
                        "api_key": "psk_fake",
                    }
                )
            )


class TestPrintStashUpload:
    def test_starts_disabled(self, host, exported):
        result = host.capabilities[0]().execute(exported)

        assert result.status == "skipped"
        assert "disabled" in result.message

    def test_ignores_geometry_steps(self, configured):
        result = configured.execute(SimpleNamespace(step=Step.posSlice))

        assert result.status == "skipped"

    def test_keeps_defaults_independent(self, host):
        first = host.capabilities[0]().get_default_config()
        first["enabled"] = True

        assert host.capabilities[0]().get_default_config()["enabled"] is False

    def test_contains_invalid_configuration(self, configured, exported, capsys):
        configured.config = '{"url":"secret-invalid-config"'

        result = configured.execute(exported)

        assert result.status == "skipped"
        assert "secret-invalid-config" not in result.message + capsys.readouterr().err

    @pytest.mark.parametrize(
        "error",
        [
            URLError("secret-network-error"),
            PermissionError("secret-permission-error"),
            TimeoutError("secret-timeout"),
        ],
        ids=["network", "permission", "timeout"],
    )
    def test_keeps_export_on_login_failure(
        self, host, configured, exported, monkeypatch, error
    ):
        def failed_request(*args, **kwargs):
            raise error

        monkeypatch.setattr(host.upload.urlrequest, "urlopen", failed_request)

        result = configured.execute(exported)

        assert result.status == "skipped"
        assert "G-code export continues" in result.message
        assert "secret" not in result.message

    def test_keeps_export_on_upload_rejection(
        self, host, configured, exported, monkeypatch
    ):
        class LoginResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return b'{"access_token":"fake-token"}'

        def response(req, **kwargs):
            if req.full_url.endswith("/auth/login"):
                return LoginResponse()
            raise HTTPError(req.full_url, 403, "Forbidden", {}, None)

        monkeypatch.setattr(host.upload.urlrequest, "urlopen", response)

        result = configured.execute(exported)

        assert result.status == "skipped"
        assert "Upload failed" in result.message

    def test_contains_missing_export_file(self, configured, tmp_path):
        result = configured.execute(export_context(tmp_path / "missing.gcode"))

        assert result.status == "skipped"
        assert "FileNotFoundError" in result.message
