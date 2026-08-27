"""Document router lifecycle and upload limits are observable through HTTP.

The cases here protect trash visibility, stable not-found responses, destructive
storage preflight, and both binary and markdown upload-size boundaries.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import Document, DocumentKind
from app.services.storage_backend import get_backend
from tests.integration.api.v1.documents.test_documents import _headers, _user

__all__ = [
    "Document",
    "DocumentKind",
    "Session",
    "TestClient",
    "_headers",
    "_overlay",
    "_user",
    "get_backend",
    "pytest",
    "utcnow",
]
