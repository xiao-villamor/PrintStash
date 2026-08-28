---
name: run-tests
description: Use when running the suites and closing what they report — a failing test to diagnose, a coverage floor to clear or raise, a red gate to get green, a merge that left tests pointing at renamed seams, or a session spent driving a suite's coverage up. Carries the lane order that fails cheapest first, the two-sided ratchet procedure, the recurring failure→cause table (ambiguous roles across breakpoints, `accept` swallowing uploads, FormData bodies, module-level caches, jsdom gaps), and how to say honestly that code is unreachable in the harness. For deciding what to test and writing it, use `create-tests` — this skill starts once tests exist.
---

# Run Tests

`create-tests` decides *what* to test and writes it. This skill is the other
half: running the suites, reading what they report, and closing the loop until
every gate is green for a reason you can state.

Load `create-tests` too whenever the answer turns out to be "write another
test" — which it usually does. The two are one workflow; the split is only that
this file knows about failure modes, floors and lanes, and never about matrices
or tiers.

## Run the lanes cheapest-first

A failure found in 8 seconds costs nothing; the same failure found after a
4-minute full run costs the whole run. So never start with `full`.

```
# backend, in order
uv run pytest <the one file you touched> -q     # seconds
./scripts/test.sh fast -q                       # ~40s, unit + integration
./scripts/test.sh full -q                       # ~3.5min, everything
uv run ruff check app/ tests/
uv run ruff format --check app/core app/db app/schemas packages/printstash-core/src
uv run pyright
./scripts/test.sh coverage                      # full run + the floor tests
```

```
# frontend, in order
pnpm test -- src/lib/__tests__/thing.test.ts    # seconds
pnpm test                                        # ~30s
pnpm lint && pnpm format:check && pnpm typecheck
pnpm coverage                                    # three suites + the gate
pnpm test:e2e                                    # Playwright, mock API
pnpm test:e2e:real                               # Playwright, real backend
```

Two things about the boundaries:

- **`ruff format --check` runs on the CI scope, not on everything.**
  `app/core app/db app/schemas packages/printstash-core/src` — checking more
  than that locally will report "failures" CI does not have.
- **`./scripts/test.sh coverage` is a superset of `full`.** It runs the same
  tests plus `tests/repo/test_coverage_floors.py`. If `full` is green and
  `coverage` is red, the floors are what moved — not the code.

### A test that fails only in a lane

Same test, green alone and red in the suite, means shared state: a module-level
cache, an `_overlay` key a previous test left set, a monkeypatch that outlived
its test, or ordering against another test's rows. Re-run it alone, then re-run
the lane, and if it does not reproduce a second time say so plainly and note the
test name rather than declaring it fixed. A flake reported honestly is data; a
flake silently re-run until green is a lie with a green tick on it.

## The recurring failures, and what each one actually is

These are the ones that cost real time in this repo. The symptom is almost never
what it looks like.

| Symptom | What it really is | Fix |
| --- | --- | --- |
| `Found multiple elements with role "button" and name "Upload"` | Tailwind's `md:` variants are not applied in jsdom, so **both** breakpoints' toolbars render at once | `screen.getAllByRole(...).at(-1)!` behind a small named helper, or scope with `within()` |
| `user.upload()` fires nothing and the rejection test sees no toast | the input's `accept` attribute filters the file out before the handler runs — `user.upload` respects it | `fireEvent.drop(el, { dataTransfer: { files: [file] } })` for the *rejection* cases; keep `user.upload` for accepted ones |
| a body assertion on an upload reads `[object FormData]` | `String(init.body)` cannot read a multipart body | capture the `FormData` at the route handler (in the fetch stub or `mock-api.ts`) and assert on `fd.get("field")` |
| second test in a file sees the first test's terminal job | task-center's terminal-job cache is **module-level** and survives the test | give each test its own job id |
| `Not implemented: navigation`, `URL.createObjectURL is not a function`, dnd-kit sensor never starts | jsdom gaps | stub in `vitest.setup.ts` — pointer capture (`setPointerCapture`/`releasePointerCapture`/`hasPointerCapture`), `URL.createObjectURL`; `window.location` needs `Object.defineProperty` |
| vitest reports `Errors 1 error` while every test passes | a promise rejected **inside** `advanceTimersByTimeAsync` with no handler yet attached | attach first: `const rejects = expect(p).rejects.toThrow(...)`, advance, then `await rejects` |
| `pytest.approx() does not support nested data structures` | `approx` is scalar/flat-sequence only | `np.testing.assert_allclose(actual, expected, atol=1e-5)` |
| a Playwright locator times out at 30s on a menu item | the visible label is not the domain term — e.g. the saved-views entry reads **"Saved views"**, not the view's name | probe once with a throwaway spec that dumps the menu's text, then fix the locator |
| a green test that asserts nothing real | the seam moved under it — e.g. `trimesh.load_mesh` → `load_scene`, so the stub patches a name nothing calls | re-read the production call site; a stub is only a stub if production still calls it |
| oxlint `anti-slop(no-module-mocking)` on a toast test | `vi.mock("sonner")` is banned | render the real `<Toaster />` via `renderApp` and assert on visible text |
| oxlint `vitest(require-mock-type-parameters)` / `require-to-throw-message` | untyped `vi.fn()`, argumentless `toThrow()` | type every mock; give `toThrow` a matcher |

**Never mechanically match the test to the code.** When a test disagrees with
the implementation, one of them is wrong and it is not always the test — but it
is also not always the code. Read the requirement. Several premises corrected
this way in one session: the sidebar tree defaults to *expanded*, `canEdit ==
isAuthenticated` in storage-config so a member *can* edit, the New-collection
button is *disabled* rather than toasting, `Checkbox` renders
`role="checkbox"` not `switch`, `formatGrams` renders `800g` not `800 g`.

## Coverage: the two-sided ratchet

Every floor in this repo fails in **both** directions. Below it is a
regression. Clear it by more than its slack and the run fails until the floor is
raised — so **a PR that improves coverage sometimes edits a floor, and that edit
is the point**, not a workaround.

Frontend slack is `slackFor(n) = max(0.5, 300/n)`; backend floors live in
`backend/tests/repo/test_coverage_floors.py` (aggregate + a per-module floor +
a capped debt list), frontend in `frontend/scripts/coverage-gate.mjs`.

The loop:

1. Run the coverage lane. Read the failure: which floor, which direction.
2. **Below** → a behaviour lost its test. Find it and restore it; do not lower
   the floor. Lowering a floor needs a sentence in the PR saying which
   behaviour was deliberately given up.
3. **Cleared** → raise the floor to just under the new number and re-run.
4. Repeat until "every floor held."

Two more ratchets work the same way and may only fall, never rise:
`MAX_CONJUNCTION_NAMES` in `frontend/tests/repo/suite-hygiene.test.ts` and
backend's `test_no_test_name_joins_two_behaviours`. When one trips, the fix is
to split the test whose name has an "and" in it — including tests you did not
write.

### Finding the next gap

The report tells you *where*, never *what*:

- `frontend/coverage/coverage-summary.json` ranks files by uncovered count.
- The istanbul HTML report gives exact uncovered line ranges per file.
- Backend's terminal report lists uncovered lines and **branch arrows**
  (`96->105`) — an arrow is a path taken, not a line missed, and needs a test
  that makes the *other* branch happen.

Then hand those line ranges to `create-tests`: the ranges say which code never
ran, and the matrix says which behaviour was never asserted. **They are not the
same question.** Coverage is produced by running the implementation, so it can
find matrix rows you forgot and can never supply them. Playwright is invisible
to all three gates.

## After a merge

A merge is the one time green tests lie at scale. Work through, in order:

1. **Stubs at renamed seams** — a stub patching a function the base branch
   renamed still passes and asserts nothing. Grep every `monkeypatch.setattr`
   and `vi.mock`/`vi.spyOn` target in the conflicted files against the current
   production call site.
2. **Tests reintroduced at pre-mirror paths** — the base may add a test at the
   old flat path. Move it into its tier directory; `tests/repo/` enforces the
   mirror, so this shows up as a repo-test failure rather than a merge conflict.
3. **Floors moved by somebody else's code** — coverage can *rise* from merged
   code that arrives with its own tests. Raise the floor; that is the same
   ratchet, not a merge artifact.
4. **Conflicts resolved by porting, not by choosing** — when both sides changed
   a test file, the behaviours from both sides survive, each in the tier it
   belongs to. `--ours`/`--theirs` on a test file silently deletes coverage.
5. **Comments the merge made false.** A comment explaining why a guard is
   unreachable is a claim the merge may have invalidated. Grep for the phrasing,
   not just the code.

## Structurally unreachable code — say it, don't hide it

Some code cannot execute in the harness at all: `stl-viewer` (three.js),
`gcode-viewer` (canvas), `pdf-viewer` (pdf.js worker), `router.tsx`'s lazy route
table. In this repo that is roughly 210 of the app suite's uncovered statements,
which puts the measurement ceiling near 97.6%.

State that as a number with the files named. Do **not** reach for `/* istanbul
ignore */`, a `# pragma: no cover`, or a lowered floor with no explanation —
each one converts a known limit into an invisible one. An honest ceiling written
down is what stops the next session from re-deriving it, and it is the only
thing that distinguishes "cannot be covered here" from "nobody has covered this
yet". The second one is work; the first one is a fact.

## Reporting a run

Give exact numbers, per lane, and never a number from a run you did not do:

> `./scripts/test.sh full -q` — 6093 passed
> `./scripts/test.sh coverage` — 93.65% branches, every floor held
> `pnpm test` — 1456 passed (107 files)
> `pnpm coverage` — app 80.75%/73.47%, domain 95.48%/92.63%, ui 98.78%/97.60%

"Tests pass" is not a result. If a lane was not run, say which and why; if
something is still red, quote it. A gate reported green without the run behind it
is the one failure this whole workflow cannot detect.
