# Preparation resource lifecycle contracts

Preparation must reserve source bytes before native admission; only active source copying uses I/O slots. Recovery may retire a receipt only after its workspace is absent. A malformed recovery identity must not choose a filesystem destination. These tests exercise real resource pools and file-backed receipts. Coverage floors, ordering rules and application code stay unchanged.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | refuses malformed recovery identities | Error | Abandoned short or nonhex receipt filename | ValueError; receipt and sentinel preserved | Integration | ✅ |
| 2 | retains recovery ownership after partial unlink failure | Error | rmtree reports missing descendant but workspace still exists | Error propagates; receipt and source bytes retained | Integration | ✅ |
| 3 | refuses workspace access outside permit lifetime | Error | Real released preparation permit | RuntimeError; no workspace access | Integration | ✅ |
| 4 | refuses preparation before pool binding | Error | No bound preparation pools | Explicit bootstrap error | Integration | ✅ |
| 5 | refuses nested preparation | Error | Active real preparation reservation | RuntimeError; original permit remains active | Integration | ✅ |
| 6 | refuses preparation after native admission | Error | Real inherited native permit | Ordering error before workspace allocation | Integration | ✅ |
| 7 | refuses I/O without preparation | Error | Bound pools, no preparation permit | Ordering error; no I/O receipt allocated | Integration | ✅ |
| 8 | refuses source I/O after native admission | Error | Real preparation plus inherited native permit | Ordering error; no I/O receipt allocated | Integration | ✅ |
| 9 | clears preparation scope after consumer failure | Error | Body raises while preparation held | Original error propagated; thread permit absent after exit | Integration | ✅ |

Validation: the affected integration file passed all 15 cases in 2.98 seconds (one third-party deprecation warning; bounded command wall time 7.08 seconds). Ten new cases exercise the nine behaviours above. Ruff import sorting was applied afterward without changing test semantics; lint, formatting and whitespace checks pass. No local coverage run was performed. The module floor must still be measured by the final GitHub Deep CI run.
