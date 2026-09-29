"""Fixtures shared by the repository invariants that read through the app."""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import Collection, CollectionRole
from tests.factories import bearer, build_collection, build_user, grant_collection_role


@pytest.fixture(params=["administrator", "granted-viewer"])
def reader(request, db_session: Session) -> tuple[dict[str, str], Collection]:
    """Headers for someone who sees the whole shared tree, and its root.

    A non-administrator reads through the RBAC scope, which is where an id list
    bound into every query used to hide (#295).
    """
    root = build_collection(db_session, "Shared")
    user = build_user(db_session, superuser=request.param == "administrator")
    grant_collection_role(db_session, user, root, CollectionRole.VIEW)
    return bearer(user), root
