# M2 closure validation

Scope: the multipart real-browser spec, this evidence record, and the existing English/Spanish linked-Model notice keys. Production behavior is unchanged; notice text no longer advertises the retired Parts only mode. Acceptance comes from [M2 in the plan](frontend-architecture/plan.md) and [the starter matrix](frontend-architecture/validation.md). Existing qualification is retained in [navigation](library-navigation-validation.md), [effective filters](library-filters-validation.md), [saved views](library-saved-views-validation.md), and [backend contracts](library-contracts-validation.md).

The prior headline selected the removed Organized control and expected linked Models to disappear. That assertion contradicts the accepted Everything / Multipart Sets contract. This source finding was not presented as an executed failure.

## Coverage matrix

This matrix was recorded with pending rows before the test correction and assessed after the final run. The rows below are checkpoints in two real lifecycles, the documented exception to one behavior per test. New M2 acceptance has a focused one-Model lifecycle so the original cover/document/deletion lifecycle retains its existing time budget.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | keeps a shared Model independently accessible — shared Model | Edge | Two sets reference the same Model | Everything renders the Model once plus both sets | Playwright | ✅ tests/e2e-real/multipart-models.spec.ts::keeps a shared Model independently accessible |
| 2 | keeps a shared Model independently accessible — mode | Happy | Everything then Multipart sets only | URL has multipart mode; set cards remain; Model cards absent | Playwright | ✅ tests/e2e-real/multipart-models.spec.ts::keeps a shared Model independently accessible |
| 3 | keeps a shared Model independently accessible — history | Happy | Open set detail from Multipart mode | Back restores multipart URL/cards; Forward reopens same detail | Playwright | ✅ tests/e2e-real/multipart-models.spec.ts::keeps a shared Model independently accessible |
| 4 | keeps a shared Model independently accessible — independent Model | Happy | Open shared Model from Everything | Model route and its recommended Revision remain accessible | Playwright | ✅ tests/e2e-real/multipart-models.spec.ts::keeps a shared Model independently accessible |
| 5 | preserves Models plus G-code after grouping deletion — retained lifecycle | Happy | Upload cover/document, favorite/tag set, delete grouping | Existing cover/document/favorite/tag assertions pass; Models remain after deletion | Playwright | ✅ tests/e2e-real/multipart-models.spec.ts::preserves Models plus G-code after grouping deletion |
| 6 | keeps a shared Model independently accessible — notice | Happy | Open New multipart set | Notice promises access in Everything; retired Parts only mode is not advertised | Playwright | ✅ tests/e2e-real/multipart-models.spec.ts::keeps a shared Model independently accessible |

## M2 acceptance assessment

- Legacy URL/preferences: qualified by the navigation matrix, including silent migration with unrelated parameters preserved.
- Malformed filters and common request/control/save projection: 203 integrated filter/grid/URL tests qualified in the filter record.
- Saved-view roundtrip: real lifecycle passed in 31.5s against fresh backend in the filter checkpoint.
- Back/Forward: navigation browser qualification already covers collection and document history; this correction adds real detail navigation. Mode changes themselves intentionally replace the current entry: `tests/e2e/vault.spec.ts::vault route / restores library mode after collection history` enters a folder, changes mode, then one Back restores the root multipart mode and sort (navigation matrix row 18, qualified in its 21-case browser run).
- Independent Model access: final real headline passed; one Model referenced by two sets appears once in Everything, opens its exact heading and recommended Revision, and remains after both sets are deleted.
- Removal: only Everything / Multipart sets only controls remain; client membership downloads are removed. Existing Model API/detail routes remain compatible; no data migration is needed to roll back the UI cutover.
- Dependency: M0 and M1 are now closed. M5 reading-position completion remains a separate downstream goal.

## Execution

The first expanded lifecycle reached the new M2 assertions but exceeded the unchanged 120-second whole-test deadline while the existing deletion was pending. Trace showed two 15-second model-card polls in the upload helper plus upload/cold-start cost; no completed DELETE response was captured. A second run was cancelled by the coordinator before qualification. The additional M2 acceptance was then split into a separate one-Model lifecycle, preserving every original lifecycle assertion and the existing timeouts. Final split run passed both lifecycles: original lifecycle 1.2m, focused M2 lifecycle 38.0s, 2 passed in 2.5m. Each kept its 120-second limit. Scoped format and lint passed; app/UI/domain type checks passed; existing locale and suite-hygiene gate passed 34 tests in 5 files (10.46s). Static evidence is `/tmp/frontend-m2-static.log`. No claim that the full frontend suite or remote CI was run by this checkpoint.

The final run used `playwright.real.config.ts`, one Chromium worker, a fresh disposable SQLite/data root, the verified official BGCODE converter, and traces retained privately. Exact selection:

```sh
pnpm exec playwright test --config=playwright.real.config.ts tests/e2e-real/multipart-models.spec.ts --grep 'preserves Models plus G-code after grouping deletion|keeps a shared Model independently accessible' --workers=1 --trace=on
```

Local evidence: `/tmp/frontend-m2-split-result.json` (complete, exit 0), `/tmp/frontend-m2-split.log`, `/tmp/frontend-m2-split-artifacts`. Failed expanded-run evidence remains in `/tmp/frontend-m2-browser.log`; the cancelled run has an explicit coordinator-cancelled manifest. Final test source SHA256: `2d9c75890041a9daad50a33c6a39a0d0523c1f5de7695691532033cdd6f7f0f8`.

M2 acceptance is qualified by this final browser result plus the linked migration, filter, saved-view, and navigation evidence. The failed oversized attempt is retained as resolved test-organization evidence, not hidden or counted as green. Broad M1/M5 completion and remote PR CI are not claimed by this checkpoint.

## Ordered acceptance (2026-10-07)

M0 closed at `9f1d6bd7`; M1 closed at `9ebdb7d8`. The prerequisite block is now
resolved. The earlier premature closure announcement remains documented in the
execution history; the existing passing evidence was retained.

After the collection cancellation change in M1, the complete ModelBrowser,
Library URL and filter tests passed **215/215 in three files** (60.51 s).
This renewed the affected consumer qualification; it did not rerun unrelated
browser lifecycles. URL/filter/saved-view owners, sidebar mode controls, locale
notices and the real multipart acceptance spec are unchanged from `371fee63`.
The real spec still has SHA256
`2d9c75890041a9daad50a33c6a39a0d0523c1f5de7695691532033cdd6f7f0f8`, matching the
successful two-lifecycle result manifest. Subsequent grid refresh changes also
have their own [qualified checkpoint](library-refresh-validation.md); the
current full grid regression above includes them.

M2 is now formally closed as a local milestone. M3 is the next active goal.
The six-row matrix above and the linked URL/filter/saved-view/navigation matrices
remain the acceptance contract. M5 scroll/snapshot work, the known pending M5
suite-hygiene failure and final PR CI are still open. No performance claim follows
from this closure. Evidence is retained locally in
`reports/frontend-implementation/m2-closure-2026-10-07`.
