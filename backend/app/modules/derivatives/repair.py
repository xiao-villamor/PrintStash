"""Re-deriving outputs an audit found broken.

Two entry points, one rule: forget the kind at its current recipe so it is
pending again, then derive it.

- ``request`` does it asynchronously, for a request path (an administrator's
  "repair" click): invalidate, commit, nudge.
- ``now`` derives synchronously, for a caller already running inside a Job
  (an audit's automatic repair), which must verify the result before it
  records the finding as repaired.
"""

from __future__ import annotations

from sqlmodel import Session, col, select

from app.core.errors import OperationError
from app.db.models import DerivativeKind, DerivativeState, File, JobKind, Model
from app.db.scopes import live
from app.db.session import get_session_factory
from app.modules.work.contracts import SkipReason

from . import policy, producers, records
from .kinds import MESH_TYPES, groups_for, recipes_for

_PRODUCERS = {
    JobKind.DERIVATIVES_MESH: producers.derive_mesh,
    JobKind.DERIVATIVES_GCODE: producers.derive_gcode,
    JobKind.DERIVATIVES_TOOLPATH: producers.derive_toolpath,
}


def representative(session: Session, model_id: int) -> File | None:
    """The Artifact whose thumbnail represents a Model (or should)."""
    model = session.get(Model, model_id)
    if model is None or model.deleted_at is not None:
        return None
    if model.thumbnail_file_id is not None:
        current = session.exec(
            select(File).where(File.id == model.thumbnail_file_id, live(File))
        ).first()
        if current is not None:
            return current
    return session.exec(
        select(File)
        .where(File.model_id == model_id, live(File))
        .order_by(col(File.file_type).in_(MESH_TYPES).desc(), col(File.id).desc())
    ).first()


def _applicable(file: File, kinds: list[DerivativeKind]) -> list[DerivativeKind]:
    recipes = recipes_for(file)
    return [kind for kind in kinds if kind in recipes]


def request(session: Session, file: File, kinds: list[DerivativeKind]) -> bool:
    """Make ``kinds`` pending for ``file`` and nudge its producers.

    ``False`` when none of them is derived for this Artifact at all (a DXF
    has no metadata derivative): there is nothing to repair it with.
    """
    from app.modules.work import nudge

    kinds = _applicable(file, kinds)
    if not kinds:
        return False
    policy.lock(session)
    for group in groups_for(file):
        if set(kinds) & set(group.kinds):
            policy.require_enabled(session, group.definition)
    records.invalidate(session, file, kinds)
    session.commit()
    groups = [group for group in groups_for(file) if set(kinds) & set(group.kinds)]
    for group in groups:
        nudge(group.definition)
    return bool(groups)


def now(
    file_id: int, kinds: list[DerivativeKind]
) -> dict[DerivativeKind, DerivativeState]:
    """Invalidate and derive ``kinds`` for one Artifact in this thread.

    Only the kinds that apply to the Artifact are derived; a kind missing from
    the result was not derived.
    """
    with get_session_factory().scoped_session() as session:
        file = session.exec(select(File).where(File.id == file_id, live(File))).first()
        if file is None:
            return {}
        policy.lock(session)
        controls = policy.resolve(session)
        allowed = {
            kind
            for group in groups_for(file)
            if controls[group.definition].enabled
            for kind in group.kinds
        }
        kinds = [kind for kind in _applicable(file, kinds) if kind in allowed]
        records.invalidate(session, file, kinds)
        session.commit()
        groups = [
            group.definition
            for group in groups_for(file)
            if set(kinds) & set(group.kinds)
        ]
    outcome: dict[DerivativeKind, DerivativeState] = {}
    for definition in groups:
        try:
            outcome.update(_PRODUCERS[definition](file_id).kinds)
        except OperationError as exc:
            if exc.code != SkipReason.DERIVATIVE_GROUP_DISABLED:
                raise
    return outcome
