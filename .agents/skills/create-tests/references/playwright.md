# Playwright tests

Three suites with different jobs. Policy is in [SKILL.md](../SKILL.md); this is
the runtime-specific how.

| Suite | Dir | Config | Backend | Job |
| --- | --- | --- | --- | --- |
| **Real** | `frontend/tests/e2e-real/` | `playwright.real.config.ts` | real uvicorn on a throwaway SQLite (`scripts/start-backend.sh`), Vite, standalone mock printer on `:7530` | the **one e2e test per headline UI capability** AGENTS.md requires; anything with persistence |
| **Mock-API** | `frontend/tests/e2e/` | `playwright.config.ts` | `mock-api.ts` (node `http` server) | route smoke: every page renders with no console/page errors; PWA |
| **Performance** | `frontend/tests/performance/` | `playwright.performance.config.ts` | `mock-api.ts` against a **production build** served by `vite preview` | records the timings a human compares between releases; asserts nothing that can fail on a slow machine |

Branch coverage never goes here — it goes to vitest. A Playwright spec proves
the marquee flow works end to end; the matrix's other rows live in the unit
layer.

## Real suite

- **One spec per feature area** (`models.spec.ts`, `collections.spec.ts`,
  `rbac.spec.ts`), named after the page/route it drives; each `test()` is one
  headline flow. Shared browser helpers live in `helpers.ts` (auth) and
  `util.ts` (upload/model/collection actions) — extend those, don't add a
  third helper file per spec.
- Import `test`/`expect` from `./helpers`, not `@playwright/test`. The `page`
  fixture arrives authenticated as the seeded admin (real JWT installed as the
  HttpOnly cookie + `printstash.user` in localStorage). For a second identity
  use `authBundleFor(username, password)` / `authedContext(browser, ...)`.
- **Serial on one DB** (`fullyParallel: false`, `workers: 1`); state is wiped
  per *launch*, not per test. So every name a spec writes is per-run unique
  (`` `e2e-model-${Date.now()}` ``) and the spec deletes what it created —
  otherwise the next local run fails on a duplicate, and later specs see your
  leftovers.
- Reuse `util.ts`: `uploadModel(page, name, { tag, collection })`,
  `modelCard(page, name)`, `clickModelAction(page, "Edit details" | "Share" |
  "Delete model")`, `createCollectionViaVault`. `gcodeFor(name)` / `stlFor(name)`
  embed the name so content-hash dedupe keeps uploads distinct — never upload
  identical bytes for two models.
- **Async ingestion**: poll with `await expect(async () => { ... }).toPass({
  timeout: 30_000 })`, never `waitForTimeout`. Per-test timeout is 120 s,
  `expect` 15 s — generous for real uploads; don't raise them further.
- Selectors are roles and labels (`getByRole("button", { name: "Upload",
  exact: true })`, `getByPlaceholder`, `getByRole("dialog")`). No CSS classes,
  no test-ids unless the primitive already exposes one.
- Confirm dialogs come from `components/ui/confirm-modal`: scope the click to
  `page.getByRole("dialog").getByRole("button", { name: "Delete" })`.
- Fleet specs use the standalone Moonraker + Spoolman emulator
  (`backend/tests/fakes/mock_printer.py`, started by
  `scripts/start-mock-printer.sh`). Add a printer pointed at
  `http://127.0.0.1:${PLAYWRIGHT_MOCK_PRINTER_PORT}`; it's a live printer with
  no hardware.
- A spec may be one long lifecycle (`models.spec.ts`: upload → edit → trash →
  restore → purge). That is the deliberate exception to one-behaviour-per-
  test: the lifecycle *is* the headline behaviour. Keep sections labelled with
  `// ── Step ──` comments so a failure points at the phase.
- After adding a spec, add its flow to the coverage list in
  `tests/e2e-real/README.md` and, if it replaces a manual step, remove that
  line from `docs/manual-testing.md`.

Run: `cd frontend && pnpm test:e2e:real` (needs `backend/.venv` or `uv`).

## Mock-API suite

- `startMockApi(apiPort)` in `beforeAll`, `resetMockApiState()` in
  `beforeEach`, close in `afterAll`. Auth is seeded through `addInitScript`
  (token + superuser in localStorage; `/auth/me` in the mock returns the same
  user).
- Adding a page or a new API read a page depends on → add the route to
  `mock-api.ts` with a realistic fixture (shape copied from a real response),
  then add the route to `app-routes.spec.ts` so it's swept by
  `collectPageProblems` (console errors + `pageerror`).
- Feature flags the mock exposes (`setExternalLibrariesEnabled`) are the only
  branching this suite does; anything deeper belongs in vitest or the real
  suite.

Run: `cd frontend && pnpm test:e2e` (`pnpm test:e2e:bundle` runs the same
specs against the experimental bundled dev server).

## Performance suite

This one is deliberately not a gate, and that is the whole design. It measures
vault load and first interaction against a **production build** — the dev
server's timings say nothing about what a self-hoster experiences — and prints
the numbers instead of asserting a threshold on them. A hard budget on a laptop
under CI load is a flake, a flaky performance gate gets disabled, and then
nobody has numbers at all.

- `repeatEach: 3`, `workers: 1`, `fullyParallel: false`: the run is a
  measurement, so it must not contend with itself.
- The only assertions are that the page actually loaded and that a metric was
  recorded at all — never a duration. If you want a regression to fail a build,
  that belongs in a different suite with a deterministic input, not here.
- Ports come from `PERF_PORT` / `PERF_API_PORT` so a perf run can sit alongside
  the other two suites.

Run: `cd frontend && pnpm test:perf`
(`pnpm test:perf:react-compiler` builds with the React Compiler on, which is
what the two `chromium-${buildMode}` project names are for: the point is
comparing the two builds' numbers).

## CI

The real and mock-API suites run per PR and in the nightly full-matrix rerun
(`.github/workflows/ci.yml`). `retries` are on in CI only; a spec that needs
the retry to pass is flaky — fix the wait, don't lean on the retry.

The performance suite runs on `workflow_dispatch` only
(`.github/workflows/tooling-experiments.yml`), which uploads its samples as an
artifact. That is deliberate: it produces numbers a human compares between
releases, so putting it on every PR would add several minutes of build time to
produce a file nobody opens.

## These specs move no coverage number

`pnpm coverage` and `frontend/scripts/coverage-gate.mjs` measure the vitest run
and nothing else. A Playwright spec — any of the three suites — is invisible to
them: the browser executes the app in a separate process the v8 provider never
instruments.

Two consequences worth holding onto:

- A page at 0% in the coverage report may be exercised by six specs here. Do not
  read a low number as untested, and do not write a shallow vitest render test to
  raise it — that adds a test for a number, which is the one reason never to add
  a test.
- Nothing cross-checks your matrix on a UI feature. For backend work an uncovered
  line is a hint that a row is missing; here there is no such hint, so the
  requirements-derived matrix is the entire safety net. Sweep the categories
  deliberately.
