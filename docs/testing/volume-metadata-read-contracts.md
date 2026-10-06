# Durable volume evidence read contracts

The volume contract in `docs/mesh-measurements.md` keeps measured, unavailable, not-calculated and legacy facts distinct. A reader must retain valid durable evidence and refuse an incompatible combination instead of certifying a legacy scalar or supplying a default. The Deep CI module report found the reader's four invalid-variant refusals and unknown-state refusal unexecuted; this change adds assertions for that owning seam only.

Valid cases use the existing factory, a real SQLite row and reload. Invalid cases detach a factory-created valid Metadata instance before modifying exactly one field, then exercise the production reader. They do not persist invalid rows, disable database checks, claim the database accepted corruption, or replace the existing real constraint tests. The detached inputs exercise the reader's defense in depth.

| # | Behaviour (test name in TestReadVolume) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | test_preserves_the_durable_volume_variant | Happy | Reload measured, unavailable, pending and legacy zero/negative/null SQLite Metadata | Returned closed variant equals the original evidence without certification or rounding | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestReadVolume::test_preserves_the_durable_volume_variant` |
| 2 | test_refuses_incoherent_detached_volume_evidence | Error | Fifteen single-field contradictions across the four closed variants | Specific invalid-persisted-variant ValueError; no read result | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestReadVolume::test_refuses_incoherent_detached_volume_evidence` |
| 3 | test_refuses_an_unknown_detached_volume_state | Error | Detached valid row with missing or unknown state | Explicit invalid-persisted-volume-state ValueError | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestReadVolume::test_refuses_an_unknown_detached_volume_state` |

Focused validation: **66 passed in8.46s**, one dependency deprecation warning, including the23 new cases and existing real persistence/constraint/recipe tests. Ruff/format/whitespace pass; imports were sorted after the run with no semantic change. No production, schema, dependency or floor changes. No local full, coverage or Deep suite ran. The global module-floor verdict remains pending GitHub validation after the other owner gaps close.
