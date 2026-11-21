"""Background work: an engine-agnostic model of Jobs, lanes, sources and steps.

See ``.agents/skills/backend-engineering/references/background-work.md``
for how to add or change a job.

- The application database records intent; the engine only executes.
- The reconciler pulls pending work from domain state and is what guarantees
  it happens; a hot path only ``nudge``s the one definition it affects.
- Every attempt rebuilds its input from the subject row; nothing is replayed.
"""

from .catalog import bound, get_catalog, get_engine
from .jobs import ActiveJobExists, jobs, safe_error, safe_item
from .submission import nudge

__all__ = [
    "ActiveJobExists",
    "bound",
    "get_catalog",
    "get_engine",
    "jobs",
    "nudge",
    "safe_error",
    "safe_item",
]
