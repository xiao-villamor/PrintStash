"""Storage keys keep every managed blob role inside its canonical namespace.

If these contracts drift, callers can lose backend portability or write new
objects under legacy/read-only locations.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.storage_backend import LocalStorageBackend


class TestLocalStorageBackendKeys:
    def test_places_capture_slots_in_the_managed_data_namespace(self) -> None:
        backend = LocalStorageBackend()

        key = backend.capture_upload_slot_key("slot-1")

        assert key == str(settings.data_dir / "capture-slots" / "slot-1")

    def test_uses_webp_for_canonical_thumbnails_and_png_for_legacy_reads(self) -> None:
        backend = LocalStorageBackend()

        canonical = backend.thumbnail_key(42)
        legacy = backend.legacy_thumbnail_key(42)

        assert canonical == str(settings.thumb_dir / "42.webp")
        assert legacy == str(settings.thumb_dir / "42.png")
