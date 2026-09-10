"""Reuse the session-bound entity factories for real-storage contracts."""

from tests.integration.conftest import (
    make_file,  # noqa: F401 — pytest fixture
    make_model,  # noqa: F401 — pytest fixture
    make_user,  # noqa: F401 — pytest fixture
)
