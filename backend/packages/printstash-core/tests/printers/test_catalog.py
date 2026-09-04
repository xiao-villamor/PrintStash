"""The provider catalog, which is what the printer setup UI is built from.

Adding a printer in PrintStash means choosing a provider and filling in fields.
Both come from here: the catalog declares each provider's capabilities, its
config fields, and the setup options the UI offers. Nothing in the frontend
hard-codes a provider, which is the point — a provider added to the core package
appears in the UI without a frontend change, and one whose capabilities change
disables the right controls automatically.

That makes two invariants worth enforcing rather than documenting.

**Every provider is in the catalog exactly once.** A provider missing from it is
unconfigurable no matter how complete its client is; a duplicate would render
twice in the picker.

**Secret fields are declared as secrets.** Access codes, API keys, and passwords
are marked `value_kind == "secret"`, and that marking is what makes the UI mask
them, keeps them out of API responses, and routes them through encryption at
rest. A field that lost the marking would appear in plain text in the settings
form and in the printer read model — this test is the only thing standing between
a renamed field and that.

`catalog_document()` is the serialized form the frontend consumes, so its key
order and vocabulary are contract: `schemaVersion` exists so a stale frontend can
detect a catalog it does not understand.
"""

from __future__ import annotations

import json

import pytest

from printstash_core.printers.catalog import (
    PROVIDER_DEFINITIONS,
    SETUP_OPTIONS,
    catalog_document,
)
from printstash_core.printers.models import Capability, ProviderId

SECRET_BEARING_PROVIDERS = [
    ProviderId.BAMBU_LAN,
    ProviderId.PRUSALINK,
    ProviderId.OCTOPRINT,
    ProviderId.ELEGOO_CENTAURI,
]


class TestProviderDefinitions:
    def test_describes_every_provider(self) -> None:
        # A provider missing here is unconfigurable however complete its client
        # is: the setup form has nothing to render.
        assert set(PROVIDER_DEFINITIONS) == set(ProviderId)

    def test_describes_each_provider_only_once(self) -> None:
        assert len(PROVIDER_DEFINITIONS) == len(ProviderId)

    def test_declares_capabilities_only_from_the_shared_vocabulary(self) -> None:
        # The UI keys off these names; an invented one would silently render no
        # control at all.
        assert all(
            definition.capabilities.supported <= frozenset(Capability)
            for definition in PROVIDER_DEFINITIONS.values()
        )

    def test_declares_moonraker_as_fully_capable(self) -> None:
        # PrintStash is Moonraker-first, and Moonraker is the reference against
        # which every other provider's gaps are described.
        assert PROVIDER_DEFINITIONS[ProviderId.MOONRAKER].capabilities.supported == (
            frozenset(Capability)
        )

    @pytest.mark.parametrize("provider_id", SECRET_BEARING_PROVIDERS)
    def test_marks_a_credential_field_as_a_secret(
        self, provider_id: ProviderId
    ) -> None:
        # The marking is what masks the field in the UI, keeps it out of the
        # printer read model, and routes it through encryption at rest.
        assert any(
            field.value_kind == "secret"
            for field in PROVIDER_DEFINITIONS[provider_id].config_fields
        )


class TestSetupOptions:
    def test_offers_a_setup_path_for_every_provider(self) -> None:
        assert {option.provider_id for option in SETUP_OPTIONS} == set(ProviderId)


class TestCatalogDocument:
    def test_declares_a_schema_version(self) -> None:
        # A stale frontend needs to be able to tell that it is looking at a
        # catalog it does not understand.
        assert catalog_document()["schemaVersion"] == 1

    def test_lists_providers_in_the_order_the_enum_declares(self) -> None:
        # Order is the order the picker renders in, so it is a decision rather
        # than an accident of dict construction.
        assert list(catalog_document()["providers"]) == [
            provider.value for provider in ProviderId
        ]

    def test_lists_every_capability_name(self) -> None:
        assert catalog_document()["capabilities"] == [
            capability.value for capability in Capability
        ]

    def test_lists_every_setup_option_by_kind(self) -> None:
        assert [option["value"] for option in catalog_document()["setupOptions"]] == [
            option.kind for option in SETUP_OPTIONS
        ]

    def test_serializes_as_json(self) -> None:
        # It is served to the browser, so anything unserializable here is a 500
        # on the printer setup page rather than a test failure.
        assert json.loads(json.dumps(catalog_document())) == catalog_document()
