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
