"""Descriptor rules shared by every transport.

Adapters decide publication safety from these declarations instead of a
transport's name, so each declaration's meaning is a contract of its own.
"""

from __future__ import annotations

import pytest

from app.modules.storage.transports import transport
from app.modules.storage.transports.base import (
    CatalogueRequirement,
    CreateStrategy,
    OpenDALService,
    StorageUse,
    TransportKind,
)
from app.modules.storage.transports.local import LocalProviderConfig
from app.modules.storage.transports.s3 import S3Transport


class TestConditionalCreate:
    @pytest.mark.parametrize(
        ("strategy", "advertised", "expected"),
        [
            pytest.param(
                CreateStrategy.CONDITIONAL_WRITE, True, True, id="write-advertised"
            ),
            pytest.param(
                CreateStrategy.CONDITIONAL_WRITE, False, False, id="write-absent"
            ),
            pytest.param(
                CreateStrategy.EXCLUSIVE_OPEN, True, True, id="exclusive-advertised"
            ),
            pytest.param(
                CreateStrategy.EXCLUSIVE_OPEN, False, False, id="exclusive-absent"
            ),
            pytest.param(CreateStrategy.WEBDAV_MOVE, False, True, id="move-unreported"),
            pytest.param(CreateStrategy.CHECKED_ONE_SHOT, True, False, id="one-shot"),
        ],
    )
    def test_follows_the_create_strategy(
        self, monkeypatch, strategy, advertised, expected
    ) -> None:
        monkeypatch.setattr(S3Transport, "create_strategy", strategy)

        assert S3Transport().conditional_create(advertised) is expected


class TestSupportedUses:
    def test_omits_vault_without_a_vault_runtime(self) -> None:
        assert transport(TransportKind.GDRIVE).supported_uses == {
            StorageUse.LIBRARY,
            StorageUse.BACKUP,
        }

    def test_includes_vault_with_a_vault_runtime(self) -> None:
        assert StorageUse.VAULT in transport(TransportKind.WEBDAV).supported_uses


class TestOpenDALService:
    def test_derives_the_cargo_feature_from_the_scheme(self) -> None:
        service = OpenDALService(scheme="azblob", label="Azure", probe_options={})

        assert service.cargo_feature == "services-azblob"

    def test_keeps_an_explicit_cargo_feature(self) -> None:
        service = OpenDALService(
            scheme="huggingface", label="HF", probe_options={}, feature="services-hf"
        )

        assert service.cargo_feature == "services-hf"


class TestResolveConfig:
    def test_rejects_another_transports_config(self) -> None:
        config = LocalProviderConfig(
            provider="local", data_dir="/data/files", thumb_dir="/data/thumbs"
        )

        with pytest.raises(TypeError, match="not a s3 config"):
            transport(TransportKind.S3).resolve_config(config)


class TestCatalogueRequirement:
    def test_serializes_a_value_rule_with_its_value(self) -> None:
        rule = CatalogueRequirement(
            kind="not_value", fields=("region",), value="auto", message="Enter it."
        )

        assert rule.as_dict() == {
            "kind": "not_value",
            "fields": ["region"],
            "value": "auto",
            "message": "Enter it.",
        }

    def test_serializes_a_field_rule_without_a_value(self) -> None:
        rule = CatalogueRequirement(
            kind="requires", fields=("passphrase", "private_key_path"), message="m"
        )

        assert rule.as_dict() == {
            "kind": "requires",
            "fields": ["passphrase", "private_key_path"],
            "message": "m",
        }
