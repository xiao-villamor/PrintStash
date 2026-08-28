"""The lookup that turns a stored provider id into a live printer client.

One registry instance owns the mapping from `ProviderId` to factory. It is
deliberately instance-owned rather than a module global: the application builds
one at startup, and tests build their own with fakes, so a test can never leak a
factory into another test or into production code.

Everything here is a refusal or a rejection, because the registry is where three
kinds of bad state get caught before they become a confusing failure somewhere
else:

- **An unknown provider id**, which is what a database row written by a newer
  PrintStash looks like after a downgrade. It has to name itself rather than
  raise `KeyError` out of a dict.
- **A config for the wrong provider.** `Printer.provider` and the credential blob
  are separate columns, so a row can say `moonraker` while holding an OctoPrint
  config. Building the wrong transport from it would produce an
  `AttributeError` on the first poll.
- **A duplicate registration**, which means two factories claim one provider and
  which of them wins would depend on import order.
"""

from __future__ import annotations

import pytest

from printstash_core.printers import (
    Capability,
    MoonrakerConfig,
    OctoPrintConfig,
    PrinterClient,
    PrinterConfig,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
    ProviderRegistry,
)

from .test_contracts import CompleteClient

CAPABILITIES = ProviderCapabilities(frozenset(Capability))


class FakeFactory:
    provider_id = ProviderId.MOONRAKER

    def __init__(
        self,
        provider_id: ProviderId = ProviderId.MOONRAKER,
        capabilities: ProviderCapabilities = CAPABILITIES,
    ) -> None:
        self.provider_id = provider_id
        self.capabilities = capabilities
        self.config: PrinterConfig | None = None

    def build(self, config: PrinterConfig) -> PrinterClient:
        self.config = config
        return CompleteClient()


class TestRegister:
    def test_registers_a_factory_under_the_id_it_declares(self) -> None:
        registry = ProviderRegistry()
        factory = FakeFactory()

        assert registry.register(factory) is factory
        assert registry.providers == (ProviderId.MOONRAKER,)

    def test_registers_a_factory_under_an_explicit_id(self) -> None:
        # Lets one factory implementation serve a second provider id — Elegoo
        # Neptune printers are Moonraker underneath.
        registry = ProviderRegistry()
        factory = FakeFactory()

        assert registry.register(ProviderId.OCTOPRINT, factory) is factory
        assert registry.providers == (ProviderId.OCTOPRINT,)

    def test_registers_every_factory_it_was_constructed_with(self) -> None:
        registry = ProviderRegistry([FakeFactory(), FakeFactory(ProviderId.OCTOPRINT)])

        assert set(registry.providers) == {
            ProviderId.MOONRAKER,
            ProviderId.OCTOPRINT,
        }

    def test_accepts_a_provider_id_spelled_as_a_string(self) -> None:
        # Ids arrive from the database as strings.
        registry = ProviderRegistry()

        registry.register("octoprint", FakeFactory())

        assert registry.providers == (ProviderId.OCTOPRINT,)

    def test_refuses_a_factory_that_declares_no_provider_id(self) -> None:
        class Anonymous:
            capabilities = CAPABILITIES

            def build(self, config: PrinterConfig) -> PrinterClient:
                return CompleteClient()

        # Registered under nothing, it would be silently unreachable — a
        # provider that exists in code and cannot be selected.
        with pytest.raises(ProviderError) as error:
            ProviderRegistry().register(Anonymous())  # type: ignore[arg-type]

        assert error.value.code == "invalid_provider_factory"

    def test_refuses_a_second_factory_for_one_provider(self) -> None:
        registry = ProviderRegistry([FakeFactory()])

        # Which factory wins would otherwise depend on import order.
        with pytest.raises(ProviderError) as error:
            registry.register(FakeFactory())

        assert error.value.code == "provider_already_registered"

    def test_refuses_an_id_that_is_not_a_provider(self) -> None:
        with pytest.raises(ProviderError) as error:
            ProviderRegistry().register("not-a-provider", FakeFactory())

        assert error.value.code == "unknown_provider"


class TestBuild:
    def test_builds_a_client_from_the_registered_factory(self) -> None:
        factory = FakeFactory()
        registry = ProviderRegistry([factory])
        config = MoonrakerConfig("http://printer.local")

        client = registry.build("moonraker", config)

        assert isinstance(client, PrinterClient)
        assert factory.config is config

    def test_accepts_a_provider_id_spelled_as_a_string(self) -> None:
        registry = ProviderRegistry([FakeFactory()])

        assert registry.build("moonraker", MoonrakerConfig("http://printer.local"))

    def test_refuses_a_configuration_for_another_provider(self) -> None:
        registry = ProviderRegistry([FakeFactory()])

        # `Printer.provider` and the credential blob are separate columns, so a
        # row can claim Moonraker while holding an OctoPrint config. Building
        # the wrong transport would fail as an AttributeError on the first poll.
        with pytest.raises(ProviderError) as error:
            registry.build(
                ProviderId.MOONRAKER, OctoPrintConfig("http://octoprint.local", "key")
            )

        assert error.value.code == "provider_config_mismatch"

    def test_refuses_a_provider_with_no_registered_factory(self) -> None:
        with pytest.raises(ProviderError) as error:
            ProviderRegistry().build(
                ProviderId.MOONRAKER, MoonrakerConfig("http://printer.local")
            )

        assert error.value.code == "unknown_provider"

    def test_refuses_an_id_that_is_not_a_provider(self) -> None:
        # What a row written by a newer PrintStash looks like after a downgrade:
        # it must name itself, not raise KeyError out of a dict.
        with pytest.raises(ProviderError) as error:
            ProviderRegistry().build(
                "not-a-provider", MoonrakerConfig("http://printer.local")
            )

        assert error.value.code == "unknown_provider"


class TestCapabilities:
    def test_reports_the_factorys_capabilities_without_building_a_client(self) -> None:
        registry = ProviderRegistry([FakeFactory()])

        # The UI asks what a provider can do while the operator is still filling
        # in the form, so the answer cannot require a connection.
        assert registry.capabilities(ProviderId.MOONRAKER) is CAPABILITIES

    def test_refuses_a_provider_with_no_registered_factory(self) -> None:
        with pytest.raises(ProviderError) as error:
            ProviderRegistry().capabilities(ProviderId.MOONRAKER)

        assert error.value.code == "unknown_provider"

    def test_refuses_an_id_that_is_not_a_provider(self) -> None:
        with pytest.raises(ProviderError) as error:
            ProviderRegistry([FakeFactory()]).capabilities("not-a-provider")

        assert error.value.code == "unknown_provider"


class TestProviders:
    def test_reports_nothing_for_an_empty_registry(self) -> None:
        assert ProviderRegistry().providers == ()

    def test_reports_registrations_in_the_order_they_were_made(self) -> None:
        registry = ProviderRegistry(
            [FakeFactory(ProviderId.OCTOPRINT), FakeFactory(ProviderId.MOONRAKER)]
        )

        assert registry.providers == (ProviderId.OCTOPRINT, ProviderId.MOONRAKER)
