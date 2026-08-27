"""Storage capability reports state what one bound adapter can guarantee.

The tier is derived from capability axes so adapters cannot quietly claim a
stronger contract with a hand-written label.
"""

from __future__ import annotations

import pytest

from app.services.storage_backend import (
    ObjectIdentity,
    StorageCapabilities,
    StorageTier,
)


class TestStorageCapabilitiesTier:
    @pytest.mark.parametrize(
        ("capabilities", "expected"),
        [
            pytest.param(
                StorageCapabilities(
                    conditional_create=True,
                    object_identity=ObjectIdentity.INODE,
                    verified_delete=True,
                    conditional_replace=True,
                    namespace_ownership=True,
                    direct_path=True,
                ),
                StorageTier.VERIFIED,
                id="verified",
            ),
            pytest.param(
                StorageCapabilities(
                    conditional_create=True,
                    object_identity=ObjectIdentity.ETAG,
                    verified_delete=False,
                    conditional_replace=True,
                    namespace_ownership=True,
                    direct_path=False,
                ),
                StorageTier.GUARDED,
                id="guarded",
            ),
            pytest.param(
                StorageCapabilities(
                    conditional_create=False,
                    object_identity=ObjectIdentity.NONE,
                    verified_delete=False,
                    conditional_replace=False,
                    namespace_ownership=True,
                    direct_path=False,
                ),
                StorageTier.UNGUARDED,
                id="unguarded",
            ),
        ],
    )
    def test_derives_the_tier_from_capability_axes(
        self,
        capabilities: StorageCapabilities,
        expected: StorageTier,
    ) -> None:
        assert capabilities.tier is expected


class TestStorageCapabilitiesWarnings:
    @pytest.mark.parametrize(
        ("capabilities", "expected"),
        [
            pytest.param(
                StorageCapabilities(
                    conditional_create=False,
                    object_identity=ObjectIdentity.INODE,
                    verified_delete=True,
                    conditional_replace=True,
                    namespace_ownership=True,
                    direct_path=True,
                ),
                "Two simultaneous uploads of the same revision can silently overwrite each other.",
                id="conditional-create",
            ),
            pytest.param(
                StorageCapabilities(
                    conditional_create=True,
                    object_identity=ObjectIdentity.NONE,
                    verified_delete=True,
                    conditional_replace=True,
                    namespace_ownership=True,
                    direct_path=True,
                ),
                "PrintStash cannot verify that a file is the one it wrote.",
                id="object-identity",
            ),
            pytest.param(
                StorageCapabilities(
                    conditional_create=True,
                    object_identity=ObjectIdentity.VERSION,
                    verified_delete=False,
                    conditional_replace=True,
                    namespace_ownership=True,
                    direct_path=False,
                ),
                "Interrupted uploads can leave files for the orphan sweep to reclaim.",
                id="verified-delete",
            ),
            pytest.param(
                StorageCapabilities(
                    conditional_create=True,
                    object_identity=ObjectIdentity.VERSION,
                    verified_delete=True,
                    conditional_replace=False,
                    namespace_ownership=True,
                    direct_path=False,
                ),
                "PrintStash cannot conditionally replace an object while its proof still matches.",
                id="conditional-replace",
            ),
            pytest.param(
                StorageCapabilities(
                    conditional_create=True,
                    object_identity=ObjectIdentity.VERSION,
                    verified_delete=True,
                    conditional_replace=True,
                    namespace_ownership=False,
                    direct_path=False,
                ),
                "PrintStash cannot confirm that a file is inside its owned storage root.",
                id="namespace-ownership",
            ),
        ],
    )
    def test_renders_the_warning_for_an_absent_safety_axis(
        self,
        capabilities: StorageCapabilities,
        expected: str,
    ) -> None:
        assert capabilities.warnings == (expected,)
