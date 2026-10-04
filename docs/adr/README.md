# Architecture decision records

One file per decision, `NNNN-kebab-slug.md`, numbered in the order the decision
was taken. An ADR records *why* a shape was chosen and what it costs — never a
tutorial for the resulting code, which belongs in `CONTEXT.md`, `AGENTS.md`, or
the module docstring.

Write one when a decision constrains future work in a way the code cannot
explain to the next reader: a seam that must stay abstract, a guarantee
deliberately not offered, a dependency admitted for a specific reason.

Superseding an ADR is normal. Mark the old one `Status: Superseded by NNNN` and
leave its reasoning intact; the record of a decision that stopped being right
is worth as much as the one that replaced it.

## Index

| # | Title | Status |
|---|-------|--------|
| 0001 | Session factory seam (referenced from `backend/app/db/session.py`) | Referenced, unwritten |
| 0002 | Runtime config overlay (referenced from `backend/app/core/config.py`) | Referenced, unwritten |
| [0003](0003-storage-capability-tiers.md) | Storage capability tiers, and OpenDAL as an additive adapter | Accepted and implemented; decision 12 superseded by 0004 |
| [0004](0004-library-sources-and-gc-safety-boundaries.md) | Read-only remote library sources and witnessed automatic GC | Accepted and implemented |
| [0005](0005-similar-models-evidence.md) | Similar Models fingerprints retrieve evidence without defining identity | Accepted; first geometry-core increment implemented |
| [0007](0007-model-families.md) | Preserve Model identity through Family relationships | Accepted and implemented |
| [0008](0008-job-engine.md) | Run background work level-triggered on a durable engine | Accepted and implemented |
| [0009](0009-3mf-loader-capabilities.md) | Preserve 3MF precision behind an explicit scene reader | Accepted direction; optional pilot only |
| [0010](0010-native-convex-hull.md) | Normalized native convex hull volume | Accepted; integrated rollout validation pending |
| [0011](0011-reusable-point-neighbors.md) | Reusable exact sample-point lookup | Accepted; integrated rollout validation pending |
| [0012](0012-local-native-resource-admission.md) | Process-shared native and prepared-source resource budgets | Implementation in progress; integrated qualification pending |

ADR-0001 and ADR-0002 are cited from code comments but were never written down.
Numbering starts at 0003 so those citations keep pointing at the decisions they
name; backfilling them is worth doing when someone next touches either seam.
