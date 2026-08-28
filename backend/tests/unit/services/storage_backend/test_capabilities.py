"""Storage capability values stay truthful across local and remote adapters.

The tier and warning payloads are operator-facing safety information. Keeping
these pure tests beside the capability unit makes regressions visible without
requiring a storage service.
"""

from __future__ import annotations

import pytest

from app.services.storage_backend import (
    ObjectIdentity,
    StorageCapabilities,
    StorageTier,
)


class TestStorageCapabilities:
    @pytest.mark.parametrize(
        ("conditional_create", "verified_delete", "conditional_replace", "tier"),
        [
            pytest.param(False, False, False, StorageTier.UNGUARDED, id="unguarded"),
            pytest.param(True, False, True, StorageTier.GUARDED, id="guarded"),
            pytest.param(True, True, True, StorageTier.VERIFIED, id="verified"),
        ],
    )
    def test_derives_the_strongest_safe_tier(
        self,
        conditional_create: bool,
        verified_delete: bool,
        conditional_replace: bool,
        tier: StorageTier,
    ) -> None:
        capabilities = StorageCapabilities(
            conditional_create=conditional_create,
            object_identity=ObjectIdentity.NONE,
            verified_delete=verified_delete,
            conditional_replace=conditional_replace,
            namespace_ownership=False,
            direct_path=False,
        )

        assert capabilities.tier is tier

    def test_serializes_capability_flags(self) -> None:
        capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.ETAG,
            verified_delete=False,
            conditional_replace=True,
            namespace_ownership=False,
            direct_path=False,
        )

        assert capabilities.as_dict() == {
            "conditional_create": True,
            "object_identity": "etag",
            "verified_delete": False,
            "conditional_replace": True,
            "namespace_ownership": False,
            "direct_path": False,
            "tier": "guarded",
            "warnings": [
                "Interrupted uploads can leave files for the orphan sweep to reclaim.",
                "PrintStash cannot confirm that a file is inside its owned storage root.",
            ],
        }

    def test_reports_all_missing_guarantee_warnings(self) -> None:
        capabilities = StorageCapabilities(
            conditional_create=False,
            object_identity=ObjectIdentity.NONE,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=False,
            direct_path=False,
        )

        assert len(capabilities.warnings) == 5
