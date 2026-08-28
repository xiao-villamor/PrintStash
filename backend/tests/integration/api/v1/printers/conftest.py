"""Package marker for the `/printers` endpoint groups.

The shared row builders live in `_helpers.py` rather than here: they take `db_session`
explicitly and are called from inside a test body, which reads better than threading two
more fixture parameters through every signature in the folder.
"""

from __future__ import annotations
