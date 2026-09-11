# Family interface audit

Scope: Family detail and two-member comparison at 1280px and 390px, using the
complete repository Benchy and a copy at half size, each with its own real
G-code Revision. This is an implementation audit, not a WCAG certification.

The initial interface used the repository's typography, semantic colors and
overlay primitives. The detector reported no deterministic findings. Visual
inspection nevertheless found material density and state-consistency problems.

| Dimension | Initial score / 4 | Evidence |
|---|---:|---|
| Accessibility | 3 | Named controls and restored keyboard focus; mobile filter text was clipped |
| Performance | 3 | Both complete Benchy meshes rendered; loading is lazy and browsing paginated; no frame-rate claim |
| Responsive design | 1 | Desktop filters expanded into long rows; mobile controls compressed into unreadable widths |
| Theming | 3 | Semantic tokens throughout; initial screenshots cover the light theme |
| Implementation integrity | 2 | Selected comparison snapshots could retain the previous canonical role |
| Total | **12 / 20** | Corrections required before handoff |

## Findings and corrections

- **P1: Unreadable mobile filters and excessive desktop form height.**
  `FamilyDetail` combined the shared input's full-width class with conflicting
  width utilities and flexible shrinking. The main controls now use a responsive
  grid; additional filters live in a labelled disclosure with an active count.
  The correction uses the repository's class-merging helper and named tokens.
- **P1: Old relationship metadata after canonical selection.** A selected member
  could retain its earlier role while a new member response was loading. The
  comparison now resolves selected identities against the latest member data.
  A regression test asserts the new roles and scales after a refetch.
- **P2: Oversized canonical summary.** The thumbnail and padding created a large
  mostly empty panel above the member list. The summary now uses a smaller image
  and compact spacing while preserving description and tag controls.

The comparison preserves physical size differences rather than fitting each
Model independently. Its semantic metadata table remains available independently
of each preview, and unknown STL/OBJ units are explained. The real-browser test
checks the projected area ratio, source hashes, independent Revisions and focus
restoration. English/Spanish unit cases cover empty, conflicting and vacant
states.

Confirmation screenshots and the final audit result remain pending. The
real-browser spec writes `family-detail-{desktop,mobile}.png` and
`family-compare-{desktop,mobile}.png` into its Playwright result directory.
