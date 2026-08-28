"""``tests/integration/`` — the real app in this process, no egress.

The default tier. SQLite with the production pragmas, real routers, real services, real
storage backend, real RBAC, real fixture files. The only things stood in for are the
outbound boundaries, and only by injection or by patching ``get_http_client`` where it is
used. The socket guard below makes that structural: a real connection fails the test, so a
test that needs one is a contract test and belongs in ``tests/contract/``.

**The `make_*` fixtures are the arrange step.** Each is a session-bound builder from
`tests/factories`, so a test says what state it needs and never threads `db_session`
through its setup. The builders are also importable for the places a fixture cannot
reach — another fixture, a `conftest.py`, a test with its own engine — and what belongs
in one is documented in `tests/factories/__init__.py`.

Adding a table to the app means adding its builder in the same PR: see
`.agents/skills/create-tests/references/fixtures.md`.
"""

from __future__ import annotations

from functools import partial
from typing import Any, Callable

import pytest
from sqlmodel import Session

from tests import factories
from tests._guards import block_real_network  # noqa: F401 — autouse
from tests.factories.protocols import (
    AGcodeArtifact,
    APrinterWithAQueue,
    GrantRole,
    HeadersFor,
    MakeArtifactLink,
    MakeCapture,
    MakeCaptureSlot,
    MakeCollection,
    MakeCover,
    MakeDocument,
    MakeExternalLibrary,
    MakeFile,
    MakeInboxItem,
    MakeModel,
    MakePrinter,
    MakePrinterFile,
    MakePrintJob,
    MakeProvenanceSource,
    MakeShareLink,
    MakeUser,
    UserHeaders,
)


def _bound(builder: Callable[..., object], session: Session) -> Any:
    """Bind a factory to this test's session so callers never pass one.

    Returns `Any` because `partial` erases the signature; each fixture then
    re-declares it through the matching protocol in `tests.factories.protocols`,
    which is what gives a test writer autocomplete and pyright a keyword to
    check. Keep a protocol in step with its builder in the same commit.
    """
    return partial(builder, session)


# --------------------------------------------------------------------------- #
# Identity and access
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_user(db_session: Session) -> MakeUser:
    """A user who can log in. Not a superuser unless you say so.

    `auth_headers` is an admin superuser and proves nothing about the 403 half of
    any endpoint's contract, so every access-control row needs one of these.
    """
    return _bound(factories.build_user, db_session)


@pytest.fixture
def headers_for() -> HeadersFor:
    """Bearer headers for an existing user, at the scope you name."""
    return factories.bearer


@pytest.fixture
def user_headers(make_user: MakeUser, headers_for: HeadersFor) -> UserHeaders:
    """Headers for a fresh non-superuser. Two identities means two calls.

    When the test also needs the row — to grant it a collection role, say — take
    `make_user` and `headers_for` instead.
    """

    def make(
        username: str | None = None,
        *,
        is_superuser: bool = False,
        scope: str = "write",
        password: str = factories.PASSWORD,
    ) -> dict[str, str]:
        user = make_user(username, superuser=is_superuser, password=password)
        return headers_for(user, scope=scope)

    return make


@pytest.fixture
def grant_role(db_session: Session) -> GrantRole:
    """Share a collection with a user, the way an admin would."""
    return _bound(factories.grant_collection_role, db_session)


@pytest.fixture
def grant_printer_role(db_session: Session) -> Any:
    """Give a user a role on one printer — separate from collection access."""
    return _bound(factories.grant_printer_role, db_session)


# --------------------------------------------------------------------------- #
# The library
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_model(db_session: Session) -> MakeModel:
    """A library model. `trashed=True` puts it in the trash."""
    return _bound(factories.build_model, db_session)


@pytest.fixture
def make_file(db_session: Session) -> MakeFile:
    """An artifact under a model, at that model's next version."""
    return _bound(factories.build_file, db_session)


@pytest.fixture
def make_metadata(db_session: Session) -> Any:
    """Slicer/mesh metadata for one artifact; every field optional."""
    return _bound(factories.build_metadata, db_session)


@pytest.fixture
def make_collection(db_session: Session) -> MakeCollection:
    """A collection. Pass `parent` to keep the materialized path consistent."""
    return _bound(factories.build_collection, db_session)


@pytest.fixture
def make_tag(db_session: Session) -> Any:
    return _bound(factories.build_tag, db_session)


@pytest.fixture
def tag_model(db_session: Session) -> Any:
    """Attach an existing tag to a model."""
    return _bound(factories.tag_model, db_session)


# --------------------------------------------------------------------------- #
# The fleet
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_printer(db_session: Session) -> MakePrinter:
    """A configured, ready printer. Name the provider and its credentials follow."""
    return _bound(factories.build_printer, db_session)


@pytest.fixture
def make_printer_file(db_session: Session) -> MakePrinterFile:
    """A file the printer reports having on its own storage."""
    return _bound(factories.build_printer_file, db_session)


@pytest.fixture
def make_print_job(db_session: Session) -> MakePrintJob:
    """A print job for an artifact; its model is derived from the artifact."""
    return _bound(factories.build_print_job, db_session)


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_provenance_source(db_session: Session) -> MakeProvenanceSource:
    """One remote source a model was captured from."""
    return _bound(factories.build_provenance_source, db_session)


@pytest.fixture
def make_capture(db_session: Session) -> MakeCapture:
    """One snapshot in a source's append-only capture history."""
    return _bound(factories.build_capture, db_session)


@pytest.fixture
def make_artifact_link(db_session: Session) -> MakeArtifactLink:
    """Attach a source-file identity to an artifact."""
    return _bound(factories.build_artifact_link, db_session)


@pytest.fixture
def make_cover(db_session: Session) -> MakeCover:
    """A source's cover row, without publishing its bytes."""
    return _bound(factories.build_cover, db_session)


# --------------------------------------------------------------------------- #
# Capture pipeline
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_inbox_item(db_session: Session) -> MakeInboxItem:
    """A pending import. Its manifest carries a complete `source` block."""
    return _bound(factories.build_inbox_item, db_session)


@pytest.fixture
def make_capture_slot(db_session: Session) -> MakeCaptureSlot:
    """An upload slot. `uploaded=True` is the state the import path looks for."""
    return _bound(factories.build_capture_slot, db_session)


@pytest.fixture
def make_inbox_result(db_session: Session) -> Any:
    """One per-selection outcome of an import."""
    return _bound(factories.build_inbox_result, db_session)


# --------------------------------------------------------------------------- #
# Operational
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_external_library(db_session: Session) -> MakeExternalLibrary:
    """A mirrored NAS folder. `scanning=True` holds a live scan claim."""
    return _bound(factories.build_external_library, db_session)


@pytest.fixture
def make_document(db_session: Session) -> MakeDocument:
    """A document; the builder fills the field set matching its `kind`."""
    return _bound(factories.build_document, db_session)


@pytest.fixture
def make_background_job(db_session: Session) -> Any:
    return _bound(factories.build_background_job, db_session)


@pytest.fixture
def make_audit_run(db_session: Session) -> Any:
    return _bound(factories.build_audit_run, db_session)


@pytest.fixture
def make_audit_finding(db_session: Session) -> Any:
    """One audit finding. An open namespace escape is a switch, not a record."""
    return _bound(factories.build_audit_finding, db_session)


@pytest.fixture
def make_share_link(db_session: Session) -> MakeShareLink:
    """A public link. Pass the raw token to endpoints; this stores only its hash."""
    return _bound(factories.build_share_link, db_session)


@pytest.fixture
def make_filament_profile(db_session: Session) -> Any:
    return _bound(factories.build_filament_profile, db_session)


@pytest.fixture
def make_notification_channel(db_session: Session) -> Any:
    """A channel. Name its events or it is subscribed to nothing."""
    return _bound(factories.build_notification_channel, db_session)


# --------------------------------------------------------------------------- #
# Scenarios — promoted only once three files needed the same shape.
# See `tests/factories/scenarios.py` for the promotion rules.
# --------------------------------------------------------------------------- #


@pytest.fixture
def a_gcode_artifact(db_session: Session) -> AGcodeArtifact:
    """A model with one recommended, known-good G-code revision."""
    return _bound(factories.a_gcode_artifact, db_session)


@pytest.fixture
def a_printer_with_a_queue(db_session: Session) -> APrinterWithAQueue:
    """A ready printer with queued jobs in a defined order."""
    return _bound(factories.a_printer_with_a_queue, db_session)


@pytest.fixture
def a_member_who_can_see_one_collection(db_session: Session) -> Any:
    """A non-superuser, one model they can reach, one they cannot."""
    return _bound(factories.a_member_who_can_see_one_collection, db_session)


# --------------------------------------------------------------------------- #
# Environments
# --------------------------------------------------------------------------- #


# Re-exported for the tests that annotate a fixture parameter: importing the
# protocol from the conftest that provides the fixture keeps the two together.
__all__ = [
    "AGcodeArtifact",
    "APrinterWithAQueue",
    "GrantRole",
    "HeadersFor",
    "MakeArtifactLink",
    "MakeCapture",
    "MakeCaptureSlot",
    "MakeCollection",
    "MakeCover",
    "MakeDocument",
    "MakeExternalLibrary",
    "MakeFile",
    "MakeInboxItem",
    "MakeModel",
    "MakePrintJob",
    "MakePrinter",
    "MakePrinterFile",
    "MakeProvenanceSource",
    "MakeShareLink",
    "MakeUser",
    "UserHeaders",
    "a_member_who_can_see_one_collection",
    "a_gcode_artifact",
    "a_printer_with_a_queue",
    "block_real_network",
    "grant_printer_role",
    "grant_role",
    "headers_for",
    "make_artifact_link",
    "make_audit_finding",
    "make_audit_run",
    "make_background_job",
    "make_capture",
    "make_capture_slot",
    "make_collection",
    "make_cover",
    "make_document",
    "make_external_library",
    "make_file",
    "make_filament_profile",
    "make_inbox_item",
    "make_inbox_result",
    "make_metadata",
    "make_model",
    "make_notification_channel",
    "make_print_job",
    "make_printer",
    "make_printer_file",
    "make_provenance_source",
    "make_share_link",
    "make_tag",
    "make_user",
    "tag_model",
    "user_headers",
]
