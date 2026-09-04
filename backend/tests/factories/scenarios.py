"""Named multi-row states that three or more test files all need.

A scenario is a *promotion*, never a first draft. The bar for adding one is:

1. Three separate test files build the same multi-row shape, and
2. the shape has a name someone would use out loud ("a printed model", "a
   printer with a queue"), and
3. every row in it is load-bearing for all three callers.

Below three, the assembly stays inline in the test that needs it — a scenario
with one caller is a helper with extra indirection, and a scenario nobody can
name is a bag of rows whose contents the reader has to go and look up anyway.
Failing (3) is the common trap: if one caller needs a row the others do not, the
scenario is really two scenarios, and merging them means every test carries setup
it does not use and readers cannot tell which rows matter.

Each function here documents *why its shape is a unit* — what breaks if a row is
missing — because that is the thing a caller cannot see from the call site. When
a scenario stops having three callers, delete it and inline it back.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.db.models import (
    File,
    FileRevisionStatus,
    FileType,
    Model,
    Printer,
    PrintJobState,
    User,
)
from tests.factories.identity import build_user, grant_collection_role
from tests.factories.library import build_collection, build_file, build_model
from tests.factories.printers import build_print_job, build_printer


def a_gcode_artifact(
    session: Session,
    name: str = "Cube",
    *,
    dispatchable: bool = False,
    **overrides: Any,
) -> File:
    """One G-code artifact under a model of its own.

    The artifact is what callers queue, print and transfer, but a `File` cannot
    exist without a model, and five test files were each creating that pair by
    hand. It returns the artifact rather than both rows because the model is a
    required parent here, not a second thing under test — reach it through
    `artifact.model_id` when a test needs it.

    `dispatchable=True` additionally marks it recommended and known-good. That is
    a *different shape*, not a nicety: the fleet dispatcher only considers a
    recommended revision and the queue endpoints only accept a known-good one, so
    a plain artifact is invisible to both. It is a flag rather than a second
    scenario because the two differ by exactly those two fields, and a pair of
    near-identical scenarios is the thing a reader cannot tell apart.
    """
    model = build_model(session, name, **overrides)
    return build_file(
        session,
        model,
        file_type=FileType.GCODE,
        filename=f"{model.slug}.gcode",
        recommended=dispatchable,
        status=FileRevisionStatus.KNOWN_GOOD if dispatchable else None,
    )


def a_printer_with_a_queue(
    session: Session, *, depth: int = 2, **overrides: Any
) -> tuple[Printer, list[File]]:
    """A ready printer with *depth* queued jobs, in queue order.

    Ordering is the point: `queue_position` is what the scheduler reads, and jobs
    created without it all sit at position 0, where "the next job" becomes
    whichever row the database happens to return first. Every reordering,
    draining and dispatch test needs a queue whose order is actually defined.
    """
    printer = build_printer(session, **overrides)
    artifacts: list[File] = []
    for position in range(depth):
        gcode = a_gcode_artifact(session, f"Queued {position + 1}")
        build_print_job(
            session,
            gcode,
            printer=printer,
            state=PrintJobState.QUEUED,
            queue_position=position,
        )
        artifacts.append(gcode)
    return printer, artifacts


def a_member_who_can_see_one_collection(
    session: Session, *, role=None
) -> tuple[User, Model, Model]:
    """A non-superuser with a grant on one of two collections.

    Returns the user, the model they can reach, and the model they cannot. Both
    halves are needed for the assertion to mean anything: a test that only builds
    the visible model passes identically against a broken filter that returns
    everything.
    """
    from app.db.models import CollectionRole

    member = build_user(session)
    visible = build_collection(session, "Visible")
    hidden = build_collection(session, "Hidden")
    grant_collection_role(session, member, visible, role or CollectionRole.VIEW)
    return (
        member,
        build_model(session, "Allowed", collection=visible),
        build_model(session, "Denied", collection=hidden),
    )
