"""Typed printer configuration: the point where a stored row becomes a client.

A `Printer` row holds a provider id and a JSON credential blob, and these
dataclasses are how that pair becomes something a transport can use. Two
properties are what the tests defend.

**Credentials are validated at construction, not at connect time.** That is
earlier and much better: an incomplete configuration fails in the settings form
the operator is looking at, rather than mid-queue when a print was supposed to
start. Every provider's required set is different — PrusaLink needs a username
*and* password in digest mode but an API key in the other, Elegoo needs an
access code only on the second-generation printer — and each of those rules is
a row here.

**Every failure looks the same from outside.** `provider_credentials_missing`,
one code and one detail, whatever was actually absent. This is deliberate: the
alternative is an error message that tells whoever is probing which field of
which provider was wrong, and there is no operator benefit to that granularity
when the settings form already shows which fields are empty.

The configs are frozen because a client caches values off them at construction;
a mutated config would leave a live client authenticating with something the
database no longer says.

`ProviderCapabilities` is here too. It is the object that decides what the UI
lets an operator click, so the `unsupported_actions` list and its stable order
are part of the API contract, not an implementation detail.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, Callable

import pytest

from printstash_core.printers import (
    BambuConfig,
    Capability,
    ElegooCentauriConfig,
    MoonrakerConfig,
    OctoPrintConfig,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
    PrusaLinkConfig,
)

# Obviously fake stand-ins for real credentials.
API_KEY = "key-123"
ACCESS_CODE = "not-a-real-code"
PASSWORD = "not-a-real-password"

VALID_CONFIGS: list[tuple[Any, ProviderId]] = [
    (MoonrakerConfig("http://printer.local"), ProviderId.MOONRAKER),
    (BambuConfig("printer.local", "SERIAL", ACCESS_CODE), ProviderId.BAMBU_LAN),
    (
        PrusaLinkConfig(
            "http://prusa.local", "digest", username="user", password=PASSWORD
        ),
        ProviderId.PRUSALINK,
    ),
    (
        PrusaLinkConfig("http://prusa.local", "api_key", api_key=API_KEY),
        ProviderId.PRUSALINK,
    ),
    (OctoPrintConfig("http://octoprint.local", API_KEY), ProviderId.OCTOPRINT),
    (
        ElegooCentauriConfig("centauri.local", "elegoo_centauri_carbon"),
        ProviderId.ELEGOO_CENTAURI,
    ),
    (
        ElegooCentauriConfig("centauri.local", "elegoo_centauri_carbon_2", ACCESS_CODE),
        ProviderId.ELEGOO_CENTAURI,
    ),
]


def all_capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(supported=frozenset(Capability))


class TestProviderId:
    def test_serializes_as_the_identifier_stored_in_the_database(self) -> None:
        # These strings are persisted on every printer row and appear in the
        # API, so renaming one is a migration, not a refactor.
        assert [provider.value for provider in ProviderId] == [
            "moonraker",
            "bambu_lan",
            "prusalink",
            "elegoo_centauri",
            "octoprint",
        ]


class TestCapability:
    def test_serializes_as_the_action_name_the_api_publishes(self) -> None:
        # The UI keys off these names to decide which controls exist.
        assert [capability.value for capability in Capability] == [
            "start",
            "pause",
            "resume",
            "cancel",
            "live_status",
            "upload",
            "list_files",
            "send_gcode",
            "measured_consumption",
            "delete_file",
            "emergency_stop",
            "server_info",
            "server_config",
            "printer_config",
            "material_state",
        ]


class TestProviderError:
    def test_defaults_to_a_generic_one_shot_failure(self) -> None:
        error = ProviderError("something went wrong")

        assert error.detail == "something went wrong"
        assert error.code == "provider_error"
        assert error.retryable is False

    def test_defaults_the_action_code_to_the_coarse_code(self) -> None:
        error = ProviderError("timed out", code="provider_timeout")

        # Never promote a remote detail string into an actionable machine code:
        # callers branch on `action_code`, and a provider's arbitrary message
        # must not become a branch target.
        assert error.action_code == "provider_timeout"

    def test_keeps_an_explicitly_named_action_code(self) -> None:
        error = ProviderError(
            "file missing", code="provider_error", action_code="bambu_ftps_not_found"
        )

        assert error.action_code == "bambu_ftps_not_found"

    def test_keeps_an_explicit_retry_opt_in(self) -> None:
        # Retries are opt-in per transport; an arbitrary provider failure stays
        # one-shot so a broken printer is not hammered.
        assert ProviderError("reset", retryable=True).retryable is True


class TestProviderCapabilities:
    def test_reports_a_declared_capability_as_supported(self) -> None:
        capabilities = ProviderCapabilities(supported=frozenset({Capability.START}))

        assert capabilities.supports(Capability.START) is True

    def test_reports_an_undeclared_capability_as_unsupported(self) -> None:
        capabilities = ProviderCapabilities(supported=frozenset({Capability.START}))

        assert capabilities.supports(Capability.PAUSE) is False

    def test_accepts_a_plain_iterable_of_capabilities(self) -> None:
        # Factories declare these as literals; normalizing to a frozenset keeps
        # the dataclass hashable and the membership test O(1).
        capabilities = ProviderCapabilities(supported=[Capability.START])  # type: ignore[arg-type]

        assert capabilities.supported == frozenset({Capability.START})

    def test_accepts_a_plain_iterable_of_support_notes(self) -> None:
        capabilities = ProviderCapabilities(
            supported=frozenset(),
            support_notes=["beta"],  # type: ignore[arg-type]
        )

        assert capabilities.support_notes == ("beta",)

    def test_defaults_to_stable_with_no_notes(self) -> None:
        capabilities = ProviderCapabilities(supported=frozenset())

        assert capabilities.support_level == "stable"
        assert capabilities.support_notes == ()

    def test_defaults_to_allowing_a_send_while_busy(self) -> None:
        # Only Bambu needs the printer idle first; requiring it everywhere would
        # block legitimate queued uploads.
        assert (
            ProviderCapabilities(supported=frozenset()).requires_ready_before_send
            is False
        )

    @pytest.mark.parametrize(
        ("flag", "capability"),
        [
            ("can_start", Capability.START),
            ("can_pause", Capability.PAUSE),
            ("can_resume", Capability.RESUME),
            ("can_cancel", Capability.CANCEL),
            ("can_live_status", Capability.LIVE_STATUS),
            ("can_upload", Capability.UPLOAD),
            ("can_list_files", Capability.LIST_FILES),
            ("can_send_gcode", Capability.SEND_GCODE),
            ("can_measure_consumption", Capability.MEASURED_CONSUMPTION),
            ("can_report_material_state", Capability.MATERIAL_STATE),
        ],
    )
    def test_exposes_each_capability_as_its_own_flag(
        self, flag: str, capability: Capability
    ) -> None:
        # These flag names are the API's field names; the UI reads them directly.
        assert getattr(ProviderCapabilities(supported=frozenset({capability})), flag)

    def test_lists_every_unsupported_action_for_an_empty_provider(self) -> None:
        capabilities = ProviderCapabilities(supported=frozenset())

        # The order is part of the API contract: it is what the UI renders as
        # "this printer cannot …", and a reshuffle changes the copy.
        assert capabilities.unsupported_actions == (
            "upload",
            "list_files",
            "delete_file",
            "send_gcode",
            "emergency_stop",
            "measured_consumption",
        )

    def test_lists_nothing_unsupported_for_a_fully_capable_provider(self) -> None:
        assert all_capabilities().unsupported_actions == ()

    def test_reports_every_action_flag_as_a_mapping(self) -> None:
        flags = all_capabilities().action_flags()

        assert set(flags) == {
            "can_start",
            "can_pause",
            "can_resume",
            "can_cancel",
            "can_live_status",
            "can_upload",
            "can_list_files",
            "can_send_gcode",
            "can_measure_consumption",
            "can_report_material_state",
        }
        assert all(flags.values())

    def test_serializes_for_the_api_with_json_ready_collections(self) -> None:
        capabilities = ProviderCapabilities(
            supported=frozenset({Capability.START}),
            support_level="beta",
            support_notes=("supervise first prints",),
        )

        payload = capabilities.as_api_dict()

        # Lists, not tuples or frozensets: this goes straight into a response.
        assert payload["support_level"] == "beta"
        assert payload["support_notes"] == ["supervise first prints"]
        assert isinstance(payload["unsupported_actions"], list)
        assert payload["can_start"] is True


class TestConfigIdentity:
    @pytest.mark.parametrize(("config", "provider_id"), VALID_CONFIGS)
    def test_names_the_provider_that_can_use_it(
        self, config: Any, provider_id: ProviderId
    ) -> None:
        # The registry dispatches on this, so a config that named the wrong
        # provider would build the wrong transport.
        assert config.provider_id is provider_id

    @pytest.mark.parametrize(("config", "provider_id"), VALID_CONFIGS)
    def test_cannot_be_mutated_after_construction(
        self, config: Any, provider_id: ProviderId
    ) -> None:
        del provider_id

        # A client caches host, credentials, and derived topics at construction.
        # A mutated config would leave it authenticating with something the
        # database no longer says.
        with pytest.raises(FrozenInstanceError):
            config.host = "elsewhere.invalid"


class TestMoonrakerConfig:
    def test_accepts_a_url_with_no_api_key(self) -> None:
        # Moonraker on a trusted LAN commonly has no key at all.
        assert MoonrakerConfig("http://printer.local").api_key is None

    def test_records_a_variant(self) -> None:
        # Elegoo Neptune 4 printers are Moonraker under a variant label.
        config = MoonrakerConfig("http://printer.local", variant="elegoo_neptune_4")

        assert config.variant == "elegoo_neptune_4"

    @pytest.mark.parametrize("base_url", ["", " ", "\t"])
    def test_refuses_a_blank_url(self, base_url: str) -> None:
        with pytest.raises(ProviderError):
            MoonrakerConfig(base_url)


class TestBambuConfig:
    def test_requires_every_bambu_connection_field(self) -> None:
        config = BambuConfig("printer.local", "SERIAL", ACCESS_CODE)

        # All three are load-bearing: the serial is the TLS identity and the
        # MQTT topic, not just a label.
        assert (config.host, config.serial, config.access_code) == (
            "printer.local",
            "SERIAL",
            ACCESS_CODE,
        )

    @pytest.mark.parametrize(
        "arguments",
        [
            ("", "SERIAL", ACCESS_CODE),
            ("printer.local", "", ACCESS_CODE),
            ("printer.local", "SERIAL", ""),
        ],
    )
    def test_refuses_a_missing_part(self, arguments: tuple[str, str, str]) -> None:
        with pytest.raises(ProviderError):
            BambuConfig(*arguments)


class TestPrusaLinkConfig:
    def test_accepts_digest_credentials(self) -> None:
        config = PrusaLinkConfig(
            "http://prusa.local", "digest", username="user", password=PASSWORD
        )

        assert config.auth_mode == "digest"

    def test_accepts_an_api_key(self) -> None:
        config = PrusaLinkConfig("http://prusa.local", "api_key", api_key=API_KEY)

        assert config.api_key == API_KEY

    @pytest.mark.parametrize(
        "keywords",
        [
            {"username": "user"},
            {"password": PASSWORD},
            {},
        ],
    )
    def test_refuses_digest_mode_without_both_halves(
        self, keywords: dict[str, str]
    ) -> None:
        # Half a digest credential authenticates as nothing, and the failure
        # would otherwise surface as a 401 that looks like a wrong password.
        with pytest.raises(ProviderError):
            PrusaLinkConfig("http://prusa.local", "digest", **keywords)

    def test_refuses_api_key_mode_without_a_key(self) -> None:
        with pytest.raises(ProviderError):
            PrusaLinkConfig("http://prusa.local", "api_key")

    def test_refuses_an_auth_mode_it_does_not_implement(self) -> None:
        # An unknown mode would otherwise send *no* credentials at all, which
        # PrusaLink answers with a 401 the operator cannot act on.
        with pytest.raises(ProviderError):
            PrusaLinkConfig("http://prusa.local", "bearer", api_key=API_KEY)

    def test_refuses_a_missing_auth_mode(self) -> None:
        with pytest.raises(ProviderError):
            PrusaLinkConfig("http://prusa.local", "")


class TestOctoPrintConfig:
    def test_requires_every_api_key_connection_field(self) -> None:
        config = OctoPrintConfig("http://octoprint.local", API_KEY)

        assert config.api_key == API_KEY

    @pytest.mark.parametrize(
        "arguments", [("", API_KEY), ("http://octoprint.local", "")]
    )
    def test_refuses_a_missing_part(self, arguments: tuple[str, str]) -> None:
        with pytest.raises(ProviderError):
            OctoPrintConfig(*arguments)


class TestElegooCentauriConfig:
    def test_accepts_the_first_generation_without_an_access_code(self) -> None:
        # The Carbon speaks unauthenticated SDCP; requiring a code would make
        # the printer unconfigurable.
        config = ElegooCentauriConfig("centauri.local", "elegoo_centauri_carbon")

        assert config.access_code is None

    def test_accepts_the_second_generation_with_an_access_code(self) -> None:
        config = ElegooCentauriConfig(
            "centauri.local", "elegoo_centauri_carbon_2", ACCESS_CODE
        )

        assert config.access_code == ACCESS_CODE

    def test_records_a_mainboard_id(self) -> None:
        config = ElegooCentauriConfig(
            "centauri.local", "elegoo_centauri_carbon", mainboard_id="board-1"
        )

        assert config.mainboard_id == "board-1"

    def test_refuses_the_second_generation_with_no_access_code(self) -> None:
        # The Carbon 2 uses authenticated MQTT. Refusing here puts the failure
        # in the settings form rather than mid-queue.
        with pytest.raises(ProviderError):
            ElegooCentauriConfig("centauri.local", "elegoo_centauri_carbon_2")

    def test_refuses_a_model_it_does_not_know(self) -> None:
        # The model decides which of two incompatible transports is opened, so
        # an unrecognised one cannot be guessed at.
        with pytest.raises(ProviderError):
            ElegooCentauriConfig("centauri.local", "elegoo_centauri_carbon_3")

    @pytest.mark.parametrize(
        "arguments", [("", "elegoo_centauri_carbon"), ("centauri.local", "")]
    )
    def test_refuses_a_missing_part(self, arguments: tuple[str, str]) -> None:
        with pytest.raises(ProviderError):
            ElegooCentauriConfig(*arguments)


class TestCredentialFailureSurface:
    @pytest.mark.parametrize(
        "factory",
        [
            lambda: MoonrakerConfig(" "),
            lambda: BambuConfig("printer.local", "", ACCESS_CODE),
            lambda: PrusaLinkConfig("http://prusa.local", "digest", username="user"),
            lambda: PrusaLinkConfig("http://prusa.local", "api_key"),
            lambda: PrusaLinkConfig("http://prusa.local", "unknown"),
            lambda: OctoPrintConfig("http://octoprint.local", ""),
            lambda: ElegooCentauriConfig("centauri.local", "unknown"),
            lambda: ElegooCentauriConfig("centauri.local", "elegoo_centauri_carbon_2"),
        ],
    )
    def test_reports_one_code_whatever_was_missing(
        self, factory: Callable[[], object]
    ) -> None:
        with pytest.raises(ProviderError) as error:
            factory()

        # Deliberately uniform. A message naming which field of which provider
        # was wrong tells a prober more than it tells an operator, who can
        # already see the empty fields in the form.
        assert error.value.detail == "provider_credentials_missing"
        assert error.value.code == "provider_credentials_missing"
