"""Migration error projection never exposes unstructured provider diagnostics."""

import pytest
from fastapi import HTTPException

from app.api.v1.vault_migration import invoke


class TestInvoke:
    @pytest.mark.parametrize(
        "message", ["credential=fake-secret", "migration_bad\nsecret"]
    )
    def test_unsafe_owner_error_is_replaced(self, message):
        def action():
            raise ValueError(message)

        with pytest.raises(HTTPException) as caught:
            invoke(action)
        assert caught.value.status_code == 409
        assert caught.value.detail == "migration_invalid_configuration"

    def test_reader_timeout_has_safe_conflict(self):
        def action():
            raise TimeoutError("unstructured provider diagnostic")

        with pytest.raises(HTTPException) as caught:
            invoke(action)
        assert caught.value.status_code == 409
        assert caught.value.detail == "migration_readers_busy"
