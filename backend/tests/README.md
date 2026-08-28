# `backend/tests` — how this suite is arranged

A test's **directory is its tier**. Nothing else — no marker, no naming convention —
decides how a test runs or what it is allowed to touch. `conftest.py` applies the tier's
rules by path, so putting a file in the wrong folder is a failure, not a style problem.

| Directory | Tier | What is real | What is stood in for |
| --- | --- | --- | --- |
| `unit/` | Unit | the function | nothing |
| `integration/` | **Integration — the default** | SQLite with the production pragmas, routers, services, storage, RBAC, real fixture files | outbound HTTP only, by injection or by patching `get_http_client` where it is used |
| `contract/` | Contract | our client against a contract-enforcing fake over a real loopback socket | nothing |
| `e2e/` | E2E | the whole app over `httpx.ASGITransport` against the fakes | nothing (`is_public_ip` relaxed for loopback) |
| `repo/` | Repo invariants | the repository itself — OpenAPI snapshot, CI config, import boundaries | nothing |

Everything that is not a mirror of a production module has one home: `fixtures/` for data
files, `fakes/` for the emulators shared by `contract/` and `e2e/`, `_data/` for the
throwaway storage root, `paths.py` for the anchors that replace `Path(__file__).parents[N]`.

## Finding a test

Translate the production path. One production module ↔ one test module, same basename:

```
app/services/ingestion.py            → integration/services/test_ingestion.py
app/api/v1/printers.py               → integration/api/v1/printers/          (folder: >600 lines)
app/services/moonraker.py            → contract/services/test_moonraker.py   (wire level)
packages/printstash-core/.../parser.py → packages/printstash-core/tests/gcode/test_parser.py
```

"Does `app/services/trash.py` have tests?" is answered by one `ls`, and auditing a module
is auditing one file. A test that cannot be placed by this rule is testing something that
is not a unit — find the unit first.

## Running it

```bash
./scripts/test.sh            # fast: unit + integration, parallel
./scripts/test.sh contract   # contract lane
./scripts/test.sh e2e        # e2e lane
./scripts/test.sh full       # everything
./scripts/test.sh affected   # only what your working tree touches
./scripts/test.sh serial     # no xdist, for debugging an ordering problem
./scripts/test.sh --help
```

Resource markers gate subsets *within* a tier: `postgres`, `s3`, and `slow` (large real
fixtures; out of the fast lane).

`postgres` and `s3` run against a real PostgreSQL and a real SeaweedFS, started as
containers by `tests/containers.py`. There is nothing to configure and no environment
variable — one definition of the image, the command and the readiness check, used by
your machine and by CI alike.

`full` therefore **needs Docker running, and fails without it.** That is deliberate: it
used to skip those 21 tests and report green, and they are the dialect-sensitive SQL, the
migration path self-hosters upgrade through, and the S3 storage and backup destinations.
A run that verified none of that should not look like a run that did. `fast` needs
nothing, and containers start only on the first test that needs one.

## Before you add one

Read `.agents/skills/create-tests/SKILL.md`. The coverage matrix it prescribes is the
definition of done, and it is derived from requirements — the issue, `CONTEXT.md`,
`docs/provider-support.md`, the router contract — never from reading the implementation,
because matrixing what the code happens to do reproduces its bugs as "expected".
