"""Row builders and named scenarios — the arrange step for the whole suite.

Every builder here takes an explicit `Session` first and **commits**. Most tests
never import them directly: `tests/integration/conftest.py` exposes the
session-bound ones as `make_*` fixtures, so a test writes `make_model("Bracket")`
and never threads a session through its arrange step. Import from here when you
need a builder somewhere a fixture cannot reach — inside another fixture, in a
`conftest.py`, or from a test that manages its own engine.

**What belongs in a builder.** One row, and the *state* a caller cares about
named as a keyword rather than as the column that encodes it: `trashed=True`
rather than `deleted_at=utcnow()`, `provider=BAMBU_LAN` rather than four
credential fields, `scanning=True` rather than a token plus an expiry plus a job
id. Where a keyword exists, it is because getting the encoding wrong produces a
row that inserts cleanly and is then invisible to the code under test — a silent
false pass, not a failure.

**What does not.** Anything that is only true for one test. Every builder takes
`**overrides` straight through to the model, so a one-off field is set at the
call site where the reader can see it, and the builder stays readable for the
next person.

`storage` covers the delete paths: `build_stored_file` writes bytes *and* the
ownership receipt that lets a purge proceed, `build_unowned_file` writes bytes
with no receipt — the "somebody else's mounted library" case every purge must
refuse. Those two are different scenarios, not a complete and an incomplete
fixture, and confusing them is a silent false pass in the most dangerous place in
the codebase.

`content` is the other half: byte builders for the *file content* a test uploads
or parses (`content.png()`, `content.gcode()`, `content.zip_bytes()`), which touch
no database. Prefer a real slicer file from `tests/fixtures/` when one will do;
reach for a builder when the test needs content shaped a particular way — over a
size limit, deliberately malformed, a PNG that lies about its dimensions.

**Rows nothing may save.** A few builders return a row and deliberately do not
commit it, because the row's *absence* from the database is the thing under test —
`detached_model`, `detached_file` and `detached_collection` feed the guards that
must refuse an id-less row, and a purge that reasoned about one would delete bytes
it has no record of. Separately, `printer_config`, `user_config` and
`print_job_config` are the configuration half of their builders without the
persistence: the contract tier has no session at all, and several pure functions
take a row and return a decision about it. `build_printer` is `printer_config`
plus `save`, so the two can never disagree about what a Bambu printer needs.

Layout mirrors the domain, not the tables: `identity` (who is asking),
`library` (models and artifacts), `printers` (the fleet), `provenance` (where a
model came from), `capture` (the inbox pipeline), `ops` (everything operational).
`scenarios` holds multi-row shapes promoted once three files needed them; read
its docstring before adding one.

Full guidance: `.agents/skills/create-tests/references/fixtures.md`.
"""

from __future__ import annotations

from tests.factories import content
from tests.factories._support import (
    nth,
    reject_aliases,
    reset_counters,
    save,
    unique_hash,
)
from tests.factories.capture import (
    build_capture_slot,
    build_inbox_item,
    build_inbox_result,
    capture_source,
    manifest_for_source,
)
from tests.factories.identity import (
    PASSWORD,
    bearer,
    build_user,
    grant_collection_role,
    grant_printer_role,
    user_config,
)
from tests.factories.library import (
    build_collection,
    build_file,
    build_metadata,
    build_model,
    build_tag,
    detached_collection,
    detached_file,
    detached_model,
    tag_model,
)
from tests.factories.ops import (
    build_audit_finding,
    build_audit_run,
    build_background_job,
    build_document,
    build_external_library,
    build_filament_profile,
    build_notification_channel,
    build_share_link,
)
from tests.factories.printers import (
    build_material_requirement,
    build_material_slot,
    build_print_job,
    build_printer,
    build_printer_file,
    build_printer_tool,
    print_job_config,
    printer_config,
)
from tests.factories.provenance import (
    build_artifact_link,
    build_capture,
    build_cover,
    build_provenance_source,
)
from tests.factories.scenarios import (
    a_gcode_artifact,
    a_member_who_can_see_one_collection,
    a_printer_with_a_queue,
)
from tests.factories.storage import (
    build_stored_file,
    build_unowned_file,
    store_owned_bytes,
)

__all__ = [
    "PASSWORD",
    "a_member_who_can_see_one_collection",
    "a_gcode_artifact",
    "a_printer_with_a_queue",
    "bearer",
    "build_artifact_link",
    "build_audit_finding",
    "build_audit_run",
    "build_background_job",
    "build_capture",
    "build_capture_slot",
    "build_collection",
    "build_cover",
    "build_document",
    "build_external_library",
    "build_file",
    "build_filament_profile",
    "build_inbox_item",
    "build_inbox_result",
    "build_material_requirement",
    "build_material_slot",
    "build_metadata",
    "build_model",
    "build_notification_channel",
    "build_print_job",
    "build_printer",
    "build_printer_file",
    "build_printer_tool",
    "build_provenance_source",
    "build_share_link",
    "build_stored_file",
    "build_tag",
    "build_unowned_file",
    "build_user",
    "detached_collection",
    "detached_file",
    "detached_model",
    "capture_source",
    "content",
    "grant_collection_role",
    "print_job_config",
    "printer_config",
    "user_config",
    "grant_printer_role",
    "manifest_for_source",
    "nth",
    "reject_aliases",
    "reset_counters",
    "save",
    "store_owned_bytes",
    "tag_model",
    "unique_hash",
]
