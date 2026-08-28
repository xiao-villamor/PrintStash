"""Conformance pack every printer provider must pass.

Add a provider to ``PROVIDERS`` and this file starts testing it. If it needs a
credential row it doesn't have here, or a capability it doesn't honour, these
tests fail — that is the point. Provider-specific behaviour (status
normalisation, wire protocols) belongs in the per-provider test modules; this
file only enforces the contract they share.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.db.models import Printer, PrinterProvider
from app.services.printer_provider import (
    _METHOD_CAPABILITY,
    PROVIDERS,
    Capability,
    DelegatingProvider,
    PrinterProviderClient,
    ProviderError,
    build_provider_registry,
    capabilities_for_provider,
    get_provider_client,
    provider_diagnostic_summary,
)
from tests.factories import printer_config

# Credentials that make each provider buildable. A new provider must add its
# row here — an entry missing from this map fails test_every_provider_is_covered.
FULL_CREDENTIALS: dict[PrinterProvider, dict[str, str]] = {
    PrinterProvider.MOONRAKER: {"moonraker_url": "http://printer.local"},
    PrinterProvider.BAMBU_LAN: {
        "bambu_host": "192.168.1.50",
        "bambu_serial": "01P00A000000001",
        "bambu_access_code": "12345678",
    },
    PrinterProvider.PRUSALINK: {
        "prusalink_url": "http://prusa.local",
        "prusalink_auth_mode": "api_key",
        "prusalink_api_key": "key",
    },
    PrinterProvider.OCTOPRINT: {
        "octoprint_url": "http://octopi.local",
        "octoprint_api_key": "key",
    },
    PrinterProvider.ELEGOO_CENTAURI: {
        "elegoo_centauri_host": "192.168.1.60",
        "provider_variant": "elegoo_centauri_carbon",
    },
}

# Sample arguments for every capability-gated provider method.
METHOD_ARGS: dict[str, tuple] = {
    "query_status": (),
    "list_files": (),
    "upload": (Path("/tmp/x.gcode"), "x.gcode"),
    "delete_file": ("x.gcode",),
    "start": ("x.gcode",),
    "pause": (),
    "resume": (),
    "cancel": (),
    "run_gcode": ("G28",),
    "emergency_stop": (),
    "server_info": (),
    "server_config": (),
    "printer_config": (),
}

ALL_PROVIDERS = list(PrinterProvider)

REGISTRY = build_provider_registry()


def _printer(provider: PrinterProvider, **overrides) -> Printer:
    fields = {**FULL_CREDENTIALS.get(provider, {}), **overrides}
    return printer_config("test", provider=provider, **fields)


def _build(provider: PrinterProvider):
    return get_provider_client(_printer(provider), registry=REGISTRY)


# The providers whose client delegates to an injectable transport. The rest reach the
# device directly, so there is nothing to make fail on cue — see
# `test_transport_errors_become_provider_errors`.
DELEGATING_PROVIDERS = [
    provider
    for provider in ALL_PROVIDERS
    if isinstance(_build(provider), DelegatingProvider)
]


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
class TestProviderConformance:
    def test_implements_the_provider_protocol(self, provider):
        assert isinstance(_build(provider), PrinterProviderClient)

    def test_missing_credentials_are_rejected(self, provider):
        with pytest.raises(ProviderError) as exc:
            get_provider_client(
                printer_config("empty", provider=provider, credentials=False),
                registry=REGISTRY,
            )
        assert exc.value.code == "provider_credentials_missing"

    def test_capability_metadata_is_honest(self, provider):
        caps = capabilities_for_provider(provider)
        assert caps.support_level in {"stable", "beta", "experimental"}
        if caps.support_level != "stable":
            assert caps.support_notes, "non-stable providers must explain themselves"
        # Every advertised gap is a capability the provider really lacks.
        for action in caps.unsupported_actions:
            assert not caps.supports(Capability(action))
        assert PROVIDERS[provider].capabilities is caps

    def test_diagnostic_summary_shape(self, provider):
        summary = provider_diagnostic_summary(provider)
        assert summary["provider"] == provider.value
        assert set(summary["capabilities"]) == set(
            capabilities_for_provider(provider).action_flags()
        )

    @pytest.mark.asyncio
    async def test_unsupported_methods_refuse_before_any_io(self, provider):
        """An undeclared capability must fail fast, not attempt a connection."""
        client = _build(provider)
        caps = capabilities_for_provider(provider)
        for method, capability in _METHOD_CAPABILITY.items():
            if caps.supports(capability):
                continue
            with pytest.raises(ProviderError) as exc:
                await getattr(client, method)(*METHOD_ARGS[method])
            assert exc.value.code == "operation_not_supported_for_provider", method

    def test_methods_are_awaitable(self, provider):
        client = _build(provider)
        for method in [*METHOD_ARGS, "info", "subscribe_status"]:
            assert inspect.iscoroutinefunction(getattr(client, method)), method


class TestProviderRegistry:
    """The registry and the enum agree about which providers exist.

    Every conformance test above is parametrized over the enum, so a provider
    added to `PrinterProvider` and not to `PROVIDERS` is not a failing test — it
    is a provider the whole conformance pack silently never runs against.
    """

    def test_every_provider_is_covered(self):
        """A new PrinterProvider enum member must be registered and credentialed."""
        assert set(PROVIDERS) == set(ALL_PROVIDERS)
        assert set(FULL_CREDENTIALS) == set(ALL_PROVIDERS)


class TestTransportFaults:
    """What a provider does when its transport raises.

    Its own group rather than a case inside `TestProviderConformance`, because it
    applies to a different set of providers: only the ones whose client delegates to
    an injectable transport have a transport to break. The class above is
    parametrized over every provider, and a member of it could only express that by
    skipping — which reports a test that was never written as a test that was
    declined.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider", DELEGATING_PROVIDERS)
    async def test_transport_errors_become_provider_errors(self, provider):
        """Client exceptions never leak past the provider boundary.

        Parametrized over `DELEGATING_PROVIDERS` rather than every provider, and
        `pytest.skip` deliberately not used: a provider with no delegating client has
        no transport to inject a fault into, so the case does not exist. Generating it
        and skipping reports a test that was never written as a test that was declined,
        and the run then counts a skip that can never become a pass.
        """
        client = _build(provider)
        error = type(client).client_error
        caps = capabilities_for_provider(provider)

        class Boom:
            def __getattr__(self, name):
                async def _raise(*args, **kwargs):
                    raise error("boom")

                return _raise

        client.client = Boom()
        for method, capability in _METHOD_CAPABILITY.items():
            if not caps.supports(capability):
                continue
            with pytest.raises(ProviderError) as exc:
                await getattr(client, method)(*METHOD_ARGS[method])
            assert exc.value.code, method
