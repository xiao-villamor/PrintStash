---
name: pickup-printstash
description: >
  Find the next actionable PrintStash issue, resume a selected issue, or recommend work from the public roadmap.
---

# Pick up PrintStash work

Read the [tracker](../../../docs/agents/issue-tracker.md) and
[roadmap](../../../docs/roadmap.md). Check named private plans if supplied.

## Find the frontier

Start with the current branch and any already selected issue. Use GitHub's current records. For an assigned issue, inspect its open
PRs, related tasks, labels, blockers, and accepted work. A pending check
or maintainer review belongs to existing work before a new implementation
unless the user asks for another priority. An open PR with green checks is
ready at that boundary while the issue remains open until merge.

For new candidates, prefer work the maintainer has prioritized and
work that unblocks an agreed roadmap outcome. Compare the number and
importance of open dependents, visible user impact, and scope readiness.
Inspect shortlisted candidates rather than taking a label or update date
as proof of readiness. Use `needs-info` for a possible grill and
`ready-for-human` for a genuinely human-owned step. Keep the difference
visible in the ranking.

## Report and hand off

Present current commitments first, then a short ranked list of candidates
with the reason, prerequisite, and first action for each. Recommend one
next step. When the user only asks what to do next, return the recommendation.
When implementation is already authorized, recover the selected issue and
continue through implement-printstash. Verify an authorized assignment or
state update after writing it.
