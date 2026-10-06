# STL worker boundary contracts

The viewer STL contract in `docs/derivatives.md` requires finite float32 facets, bounded streaming output and verified source identity before a success manifest. These tests retain actual files and exporter behaviour, with post-export filesystem changes driven at the external Trimesh exporter seam. The 256 MiB ceiling is tested at its actual value.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | refuses_nonfinite_source_facets | Error | NaN/infinite actual vertices | INVALID_SOURCE; source arrays unchanged | Integration | ✅ |
| 2 | refuses_stream_write_above_real_ceiling | Error | Actual 256 MiB stream then one byte | RESOURCE_LIMIT; physical length/counter unchanged | Integration | ✅ |
| 3 | refuses_changed_source_digest | Error | Wrong expected SHA256 | SOURCE_CHANGED; no output/source mutation | Integration | ✅ |
| 4 | reports_missing_source_storage | Error | Absent actual source path | STORAGE frame; no output | Integration | ✅ |
| 5 | reports_source_disappearance_after_export | Error | Source unlinked after delegated real export | STORAGE frame; converted bytes not success | Integration | ✅ |
| 6 | refuses_source_mutation_after_export | Error | Source changed after delegated real export | SOURCE_CHANGED; no success manifest | Integration | ✅ |

Validation: the affected integration file passed 18 tests with one preserved deprecation warning in 7.18 seconds (16.57 seconds including the bounded runner). Seven new cases cover these six behaviours. Ruff check, format and whitespace checks passed. No production code, dependencies, budgets or coverage floors changed. The final measured module floor remains pending GitHub Deep CI. Rejected staged output is retained for its recovery owner; these assertions do not claim publication or global cleanup.
