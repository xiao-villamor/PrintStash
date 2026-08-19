from __future__ import annotations

from printstash_core.printers import Capability, ProviderCapabilities, ProviderId


def test_provider_ids_match_current_provider_vocabulary() -> None:
    assert [provider.value for provider in ProviderId] == [
        "moonraker",
        "bambu_lan",
        "prusalink",
        "elegoo_centauri",
        "octoprint",
    ]


def test_capabilities_match_current_action_vocabulary() -> None:
    assert {capability.value for capability in Capability} == {
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
    }


def test_capability_flags_and_unsupported_actions_remain_compatible() -> None:
    capabilities = ProviderCapabilities(
        supported=frozenset({Capability.START, Capability.LIVE_STATUS}),
        support_level="beta",
        support_notes=("Hardware validation is pending.",),
        requires_ready_before_send=True,
    )

    assert capabilities.can_start is True
    assert capabilities.can_live_status is True
    assert capabilities.can_upload is False
    assert capabilities.requires_ready_before_send is True
    assert capabilities.unsupported_actions == (
        "upload",
        "list_files",
        "delete_file",
        "send_gcode",
        "emergency_stop",
        "measured_consumption",
    )
    assert capabilities.as_api_dict() == {
        **capabilities.action_flags(),
        "support_level": "beta",
        "support_notes": ["Hardware validation is pending."],
        "unsupported_actions": list(capabilities.unsupported_actions),
    }


def test_capability_collections_are_defensively_frozen() -> None:
    supported = {Capability.START}
    notes = ["beta"]
    capabilities = ProviderCapabilities(  # type: ignore[arg-type]
        supported=supported, support_notes=notes
    )

    supported.add(Capability.CANCEL)
    notes.append("changed")

    assert capabilities.supported == frozenset({Capability.START})
    assert capabilities.support_notes == ("beta",)
