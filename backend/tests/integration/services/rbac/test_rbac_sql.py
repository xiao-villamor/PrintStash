"""``accessible_collection_ids`` moved from a Python scan to one SQL query.

The old implementation loaded every live collection and every grant and matched
them pairwise in Python. It is kept here as an oracle: the query must return
exactly the same set on any tree, or someone silently gains or loses access.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    User,
)
from app.db.scopes import live
from app.services import rbac, taxonomy
from app.services.rbac import ROLE_ORDER, role_allows
from tests.factories import (
    build_collection,
    build_user,
    grant_collection_role,
)


def _oracle(
    session: Session,
    user: User,
    minimum: CollectionRole = CollectionRole.VIEW,
) -> set[int]:
    """The pre-rewrite Python implementation, verbatim."""
    collections = list(session.exec(select(Collection).where(live(Collection))).all())
    if user.is_superuser:
        return {int(c.id) for c in collections if c.id is not None}

    grants = session.exec(
        select(CollectionPermission, Collection)
        .join(Collection, Collection.id == CollectionPermission.collection_id)
        .where(CollectionPermission.user_id == user.id, live(Collection))
    ).all()
    out: set[int] = set()
    for collection in collections:
        if collection.id is None:
            continue
        for permission, granted_collection in grants:
            inherited = (
                collection.path == granted_collection.path
                or collection.path.startswith(granted_collection.path + "/")
            )
            if inherited and role_allows(permission.role, minimum):
                out.add(collection.id)
                break
    return out


def _grant(
    session: Session, user: User, collection_id: int, role: CollectionRole
) -> None:
    grant_collection_role(session, user, collection_id, role)


def _seed_tree(session: Session) -> dict[str, Collection]:
    """A tree with the shapes that break naive prefix matching.

    ``parts`` and ``parts-extra`` share a string prefix but are siblings: a
    grant on ``parts`` must never reach ``parts-extra``.
    """
    paths = [
        "Parts",
        "Parts/Brackets",
        "Parts/Brackets/Small",
        "Parts/Gears",
        "Parts Extra",
        "Toys",
        "Toys/Dragons",
    ]
    return {p: taxonomy.resolve_or_create_collection(session, p) for p in paths}


class TestAccessibleCollectionIds:
    def test_sql_matches_python_for_superuser(self, db_session: Session) -> None:
        _seed_tree(db_session)
        root = build_user(db_session, "root", superuser=True)

        assert rbac.accessible_collection_ids(db_session, root) == _oracle(
            db_session, root
        )

    def test_sql_matches_python_with_no_grants(self, db_session: Session) -> None:
        _seed_tree(db_session)
        nobody = build_user(db_session, "nobody")

        assert rbac.accessible_collection_ids(db_session, nobody) == set()
        assert rbac.accessible_collection_ids(db_session, nobody) == _oracle(
            db_session, nobody
        )

    @pytest.mark.parametrize(
        "minimum", [CollectionRole.VIEW, CollectionRole.EDIT, CollectionRole.ADMIN]
    )
    def test_sql_matches_python_on_nested_grants(
        self, db_session: Session, minimum: CollectionRole
    ) -> None:
        tree = _seed_tree(db_session)
        user = build_user(db_session, "alice")
        _grant(db_session, user, tree["Parts/Brackets"].id, CollectionRole.EDIT)
        _grant(db_session, user, tree["Toys"].id, CollectionRole.VIEW)

        assert rbac.accessible_collection_ids(db_session, user, minimum) == _oracle(
            db_session, user, minimum
        )

    def test_grant_does_not_leak_to_prefix_sibling(self, db_session: Session) -> None:
        """ "Parts" must not grant "Parts Extra" — they only share a string prefix."""
        tree = _seed_tree(db_session)
        user = build_user(db_session, "bob")
        _grant(db_session, user, tree["Parts"].id, CollectionRole.VIEW)

        got = rbac.accessible_collection_ids(db_session, user)

        assert tree["Parts Extra"].id not in got
        assert tree["Parts/Brackets"].id in got, "descendants must inherit"
        assert got == _oracle(db_session, user)

    def test_highest_grant_wins_across_overlapping_scopes(
        self, db_session: Session
    ) -> None:
        tree = _seed_tree(db_session)
        user = build_user(db_session, "carol")
        _grant(db_session, user, tree["Parts"].id, CollectionRole.VIEW)
        _grant(db_session, user, tree["Parts/Brackets"].id, CollectionRole.ADMIN)

        admin_ids = rbac.accessible_collection_ids(
            db_session, user, CollectionRole.ADMIN
        )

        assert tree["Parts/Brackets"].id in admin_ids
        assert tree["Parts/Brackets/Small"].id in admin_ids
        assert tree["Parts"].id not in admin_ids, (
            "the weaker grant must not be upgraded"
        )
        assert admin_ids == _oracle(db_session, user, CollectionRole.ADMIN)

    def test_like_metacharacters_in_path_do_not_widen_the_grant(
        self,
        db_session: Session,
    ) -> None:
        """A path containing ``_`` is a literal, not the SQL single-char wildcard.

        ``slugify`` strips both ``_`` and ``%`` today, so the paths are written
        directly: going through the taxonomy helper would silently sanitise the
        metacharacter and leave the escaping untested.
        """
        granted = build_collection(db_session, name="a_b", slug="a_b", path="a_b")
        sibling_child = build_collection(
            db_session, name="inner", slug="inner", path="axb/inner"
        )
        granted_child = build_collection(
            db_session, name="inner", slug="inner", path="a_b/inner"
        )
        db_session.add_all([granted, sibling_child, granted_child])
        db_session.commit()
        for c in (granted, sibling_child, granted_child):
            db_session.refresh(c)

        user = build_user(db_session, "erin")
        _grant(db_session, user, granted.id, CollectionRole.VIEW)

        got = rbac.accessible_collection_ids(db_session, user)

        assert granted.id in got and granted_child.id in got
        assert sibling_child.id not in got, "'_' matched any char — LIKE was unescaped"
        assert got == _oracle(db_session, user)

    def test_sql_matches_python_on_a_wide_tree(self, db_session: Session) -> None:
        """The shape the rewrite exists for: many collections, several grants.

        The old code compared every collection against every grant in Python. This
        is the case where that stopped being free — and where an off-by-one in the
        prefix match would show up.
        """
        tree = _seed_tree(db_session)
        user = build_user(db_session, "frank")
        _grant(db_session, user, tree["Parts/Brackets"].id, CollectionRole.EDIT)
        _grant(db_session, user, tree["Toys"].id, CollectionRole.VIEW)

        for i in range(60):
            taxonomy.resolve_or_create_collection(db_session, f"Parts/Brackets/Bulk{i}")
            taxonomy.resolve_or_create_collection(db_session, f"Parts Extra/Bulk{i}")

        got = rbac.accessible_collection_ids(db_session, user, CollectionRole.EDIT)
        assert got == _oracle(db_session, user, CollectionRole.EDIT)
        assert len(got) == 62, "60 bulk children + Brackets + Small"

    def test_trashed_collection_is_excluded(self, db_session: Session) -> None:
        from app.core.time import utcnow

        tree = _seed_tree(db_session)
        user = build_user(db_session, "dave")
        _grant(db_session, user, tree["Parts"].id, CollectionRole.VIEW)

        brackets = tree["Parts/Brackets"]
        brackets.deleted_at = utcnow()
        db_session.add(brackets)
        db_session.commit()

        got = rbac.accessible_collection_ids(db_session, user)
        assert brackets.id not in got
        assert got == _oracle(db_session, user)


class TestRoleOrder:
    def test_ranks_every_role_the_enum_declares(self) -> None:
        # `role_allows` indexes `ROLE_ORDER` by role. A role added to the enum and
        # not to the map raises `KeyError` inside a permission check — which fails
        # closed only by accident, and 500s the request either way.
        assert set(ROLE_ORDER) == set(CollectionRole)
