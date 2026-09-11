# Family interface audit

Scope: Family creation/joining, Model overview, collapsed library, Family detail
and two-member comparison at 1280px and 390px. The comparison uses the complete
repository Benchy and a copy at half size, each with its own real G-code
Revision. Family detail is also inspected in dark mode. This is an implementation
audit, not a WCAG certification or a browser frame-rate benchmark.

| Dimension | Initial score / 4 | Final score / 4 | Evidence |
|---|---:|---:|---|
| Accessibility | 3 | 3 | Named controls, semantic comparison table, restored keyboard focus, readable filter labels and a named back link; no complete assistive-technology audit |
| Performance | 3 | 3 | Both complete Benchy meshes render; lazy previews and server pagination; no frame-rate claim |
| Responsive design | 1 | 3 | Compact desktop controls, readable mobile filters and complete long Model titles at both tested widths; other viewports remain unmeasured |
| Theming | 3 | 4 | Inspected light and dark Family detail with semantic foreground/background tokens; no new hardcoded theme colors |
| Implementation integrity | 2 | 4 | Refetched canonical metadata, independent preview errors, physical scale and bounded navigation verified by observable tests |
| Total | **12 / 20** | **17 / 20** | Initial findings resolved; stated audit limits remain |

## Findings and corrections

- **P1, resolved: Unreadable mobile filters and excessive desktop form height.**
  Shared input width and flex shrinking compressed controls. The main controls
  now use a responsive grid; additional filters live in a labelled disclosure
  with an active count. The correction uses the existing class-merging helper.
- **P1, resolved: Old relationship metadata after canonical selection.**
  Comparison selection now resolves member identities against the latest server
  data. A regression test asserts the new roles and scales after refetch.
- **P2, resolved: Oversized canonical summary.** A smaller thumbnail and compact
  spacing preserve description and tag controls without a mostly empty panel.
- **P2, resolved: Duplicate canonical navigation in Model overview.** The
  canonical appears once and is excluded from other variations. The current
  canonical has no self-link. At most three other variations appear, followed
  by navigation to the complete Family when more members exist.
- **P2, resolved: Long Model title clipped on mobile.** The identity column can
  shrink and wrap; actions occupy their own row on narrow screens. The browser
  test measures the full title's text rectangles, rather than only its element
  box, and checks that the page does not overflow horizontally.
- **P3, resolved: Comparison field label broke mid-word on mobile.** The field
  column receives more width on small screens. The table retains semantic row
  headings and both Model values.

## Confirmation

The static impeccable detector reports **zero deterministic findings** across
the changed Family surfaces, Model header, browsing and comparison/viewer files.
That result is separate from the visual inspection above: the initial detector
also reported zero while the screenshots exposed the listed issues.

Real-browser assertions cover projected mesh area at a shared physical scale,
source hashes, independent Revisions, canonical printing, explicit Multipart
Choices, unsupported and failed previews, missing dimensions, focus restoration,
long-title bounds and horizontal overflow. English/Spanish unit cases cover
empty, conflicting and vacant states. The overview tests also cover a sole
canonical and a larger Family without repeating navigation targets.

Screenshots produced by the real-browser specs are retained in the Playwright
result directory: `family-join-{desktop,mobile}.png`,
`family-overview-{desktop,mobile}.png`, `family-library-{desktop,mobile}.png`,
`family-detail-{desktop,mobile}.png`, `family-compare-{desktop,mobile}.png` and
`family-detail-dark.png`. Visual readback confirms the corrections at both
widths; the mobile comparison preserves both complete meshes and readable field
labels. The deterministic assertions accompany these screenshots.
