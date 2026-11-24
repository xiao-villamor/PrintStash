"""The registry is the one place a transport is declared.

Every other layer (catalogue, forms, adapters, receipts, connection parsing,
the image build) reads the registry, so a transport that is missing from it,
or registered twice, would silently disappear from one of those layers.
"""

from __future__ import annotations

from typing import get_args

import pytest

from app.db.models import LibrarySourceKind
from app.modules.storage.transports import (
    TRANSPORTS,
    StorageProviderConfig,
    config_models,
    remote_transport,
    remote_transports,
    transport,
    transport_for_config,
)
from app.modules.storage.transports.base import ProviderConfig, TransportKind
from app.modules.storage.transports.presets import PRESETS


class TestTransports:
    def test_registers_every_transport_kind_once(self) -> None:
        kinds = [registered.kind for registered in TRANSPORTS]

        assert sorted(kinds) == sorted(TransportKind)

    def test_provider_config_union_lists_the_registered_models(self) -> None:
        union = get_args(get_args(StorageProviderConfig)[0])

        assert set(union) == set(config_models())

    def test_gives_each_provider_id_one_transport(self) -> None:
        provider_ids = [
            provider_id
            for model in config_models()
            for provider_id in get_args(model.model_fields["provider"].annotation)
        ]

        assert len(provider_ids) == len(set(provider_ids))

    def test_names_a_registered_transport_for_every_preset(self) -> None:
        for preset_id, preset in PRESETS.items():
            registered = transport(TransportKind(preset["transport"]))
            accepted = get_args(
                registered.config_model.model_fields["provider"].annotation
            )
            assert preset_id in accepted


class TestTransportLookup:
    @pytest.mark.parametrize("kind", list(TransportKind), ids=str)
    def test_finds_the_descriptor_for_a_kind(self, kind: TransportKind) -> None:
        assert transport(kind).kind is kind

    def test_rejects_local_as_a_remote_transport(self) -> None:
        with pytest.raises(ValueError, match="not a remote transport"):
            remote_transport(TransportKind.LOCAL)

    def test_lists_every_network_transport_as_remote(self) -> None:
        assert {t.kind for t in remote_transports()} == set(TransportKind) - {
            TransportKind.LOCAL
        }


class TestTransportForConfig:
    @pytest.mark.parametrize("registered", TRANSPORTS, ids=lambda t: t.kind.value)
    def test_resolves_a_config_to_its_transport(self, registered) -> None:
        provider = registered.default_provider
        config = registered.config_model.model_construct(provider=provider)

        assert transport_for_config(config) is registered

    def test_rejects_an_unregistered_config_model(self) -> None:
        class Unregistered(ProviderConfig):
            pass

        with pytest.raises(ValueError, match="storage_provider_unknown"):
            transport_for_config(Unregistered.model_construct())


class TestConnectionKinds:
    def test_library_source_kinds_are_mounted_plus_the_remote_transports(self) -> None:
        # Connection rows persist this enum (TEXT + CHECK); a remote transport
        # without a member cannot be saved, a member without one cannot load.
        assert {kind.value for kind in LibrarySourceKind} == {"mounted"} | {
            t.kind.value for t in remote_transports()
        }
