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
| [0003](0003-storage-capability-tiers.md) | Storage capability tiers, and OpenDAL as an additive adapter | Accepted and implemented |

ADR-0001 and ADR-0002 are cited from code comments but were never written down.
Numbering starts at 0003 so those citations keep pointing at the decisions they
name; backfilling them is worth doing when someone next touches either seam.
