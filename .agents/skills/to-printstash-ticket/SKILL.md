---
name: to-printstash-ticket
description: >
  Turn an agreed PrintStash idea into a GitHub issue, split a plan into implementation tickets, or capture a follow-up from active work.
---

# To a PrintStash ticket

Use [GitHub tracker guidance](../../../docs/agents/issue-tracker.md). Search
existing issues and related docs before drafting. Keep private evidence in
`reports/`; use public-safe reproduction details and observations in issues.

- **Plan:** use [grill-printstash](../grill-printstash/SKILL.md) for unresolved
  requirements. State the goal, scope, decisions, observable acceptance
  criteria, and prerequisites. Use [to-tickets](../to-tickets/SKILL.md) for an
  approved plan that benefits from multiple independently deliverable slices.
- **Quick capture:** record the observed behavior, impact, relevant public
  paths, and next action. Link a matching issue if one already owns the work.
  Use `needs-info` or `needs-triage` when the contract is still open.
- **Publish:** create or edit issues only as authorized. Link related issues
  and real blocking edges. Use the existing [triage labels](../../../docs/agents/triage-labels.md).
  Report the URLs and any outstanding decisions. Draft-only requests end with
  the issue text for the user.

Pass a selected ready issue to [implement-printstash](../implement-printstash/SKILL.md)
when implementation is requested.

## Build the issue contract

Use the existing GitHub categories and templates where appropriate. A bug
report should give current and expected behavior, a reproducible scenario,
and affected configuration or versions. A feature issue should describe
the user outcome, scope, and what would demonstrate acceptance. A research
or decision issue should say what remains uncertain, what evidence can
resolve it, and where the settled answer goes.

Include these sections when they apply:

- **Outcome** describes the independently usable result.
- **Scope and decisions** records material decisions and excludes unrelated
  improvements from the accepted work.
- **Acceptance criteria** are observable conditions, including failure or
  compatibility evidence the implementer must prove.
- **Blocked by** identifies requirements that genuinely must land first.
  Use native relations where available.
- **Documentation** identifies guides changed by the delivered behavior
  and any decisions that still need a durable home.

Slice a larger feature by demonstrable outcomes through the needed layers.
Each issue should leave a usable or verifiable result and name its
dependencies. Use a parent for a shared objective and native blocking
edges for real ordering. For a wide mechanical migration, follow the
upstream expand–contract route where independent vertical slices cannot
remain green.

## Prevent duplicate work

Before quick capture, check whether an existing issue already owns
the observed problem. Prefer an authorized update with the new evidence
to a duplicate issue. During active work, retain the separate discovery
without making it a blocker unless it actually prevents acceptance.

Verify any public write by reading back the issue and links. Report
readiness, what remains unresolved, and the resulting URL.
