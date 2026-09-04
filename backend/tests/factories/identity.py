"""Builders for who is asking: users, tokens, and the two RBAC grants.

The suite's default `auth_headers` is an admin superuser, which proves nothing
about the 403 half of any endpoint's contract. Every access-control row needs a
second identity, and hand-rolling one is how thirteen slightly different `_user`
helpers came to exist — with `superuser` defaulting to `True` in one file and
`False` in others, so the same call meant opposite things depending on which file
you were reading. Here it defaults to `False`: a plain user is the interesting
case, and a superuser is something a test asks for out loud.

Passwords are hashed through the production hasher rather than stubbed, because
several tests drive the real login endpoint. `PASSWORD` is deliberately obvious
filler — no test in this suite should contain anything resembling a real
credential.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    Printer,
    PrinterPermission,
    PrinterRole,
    User,
)
from app.services.auth import create_access_token, hash_password
from tests.factories._support import nth, reject_aliases, save

PASSWORD = "Password123"


def user_config(
    username: str | None = None,
    *,
    superuser: bool = False,
    active: bool = True,
    password: str = PASSWORD,
    password_hash: str | None = None,
    **overrides: Any,
) -> User:
    """A `User` that is deliberately *not* saved.

    A few checks are pure logic over an identity — the scope a token grants, the
    403 half of a dependency — and giving them a session would be inventing a
    database they do not use. They still need the same defaults, above all
    `superuser=False`, because a test that accidentally holds an admin passes
    every access check it meant to be refused by.

    `password_hash=` stores a hash verbatim instead of hashing `password`. It is
    for the upgrade path only — a row written by an older release's hasher, which
    the current one cannot produce — and it is a separate argument from
    `password=` so that a caller cannot pass a plaintext into the hash column by
    getting one keyword wrong.
    """
    reject_aliases(
        overrides,
        {
            "is_superuser": "superuser",
            "is_active": "active",
            "hashed_password": "password (or password_hash for a verbatim hash)",
        },
    )
    return User(
        username=username or f"user-{nth('user')}",
        hashed_password=password_hash or hash_password(password),
        is_active=active,
        is_superuser=superuser,
        **overrides,
    )


def build_user(
    session: Session,
    username: str | None = None,
    *,
    superuser: bool = False,
    active: bool = True,
    password: str = PASSWORD,
    password_hash: str | None = None,
    **overrides: Any,
) -> User:
    """A user who can log in. Not a superuser unless you say so."""
    reject_aliases(
        overrides,
        {
            "is_superuser": "superuser",
            "is_active": "active",
            "hashed_password": "password (or password_hash for a verbatim hash)",
        },
    )
    return save(
        session,
        user_config(
            username,
            superuser=superuser,
            active=active,
            password=password,
            password_hash=password_hash,
            **overrides,
        ),
    )


def bearer(user: User, *, scope: str | None = None) -> dict[str, str]:
    """Authorization headers for *user*, at the scope they would actually get.

    The default mirrors login: `admin` for a superuser, `write` otherwise. Five
    byte-identical `_headers(user)` helpers were deriving exactly this, and
    getting it wrong means an admin endpoint 403s for a reason that has nothing
    to do with the behaviour under test.

    Scope stays separate from the role, so name it when the *scope* is what is
    under test — a `read` token belonging to a superuser is a real case, and
    several endpoints are gated on the scope rather than on who the caller is.
    """
    if scope is None:
        scope = "admin" if user.is_superuser else "write"
    token = create_access_token(user.id, user.username, scope=scope)
    return {"Authorization": f"Bearer {token}"}


def grant_collection_role(
    session: Session,
    user: User,
    collection: Collection | int,
    role: CollectionRole = CollectionRole.VIEW,
) -> CollectionPermission:
    """Share a collection with a user, the way an admin would.

    Roles are hierarchical (`view` < `edit` < `admin`) and resolve down the
    collection tree, so granting on a parent is how a test covers inheritance.

    An id is accepted as well as a row because several callers only ever hold the
    id — a collection resolved by path, or one read back out of a response body —
    and making them re-fetch the row to grant on it is friction with no payoff.
    """
    collection_id = collection if isinstance(collection, int) else collection.id
    return save(
        session,
        CollectionPermission(user_id=user.id, collection_id=collection_id, role=role),
    )


def grant_printer_role(
    session: Session,
    user: User,
    printer: Printer,
    role: PrinterRole = PrinterRole.PRINT,
) -> PrinterPermission:
    """Give a user a role on one printer.

    Separate from collection access: someone who may print a model is not
    necessarily someone who may reconfigure the machine. Roles are ordered
    `view` < `print` < `control` < `admin`.
    """
    return save(
        session,
        PrinterPermission(user_id=user.id, printer_id=printer.id, role=role),
    )
