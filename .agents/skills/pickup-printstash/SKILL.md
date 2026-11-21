---
name: pickup-printstash
description: >
  Find the next actionable PrintStash issue, resume a selected issue, or recommend work from the public roadmap.
---

# Pick up PrintStash work

Read the [tracker](../../../docs/agents/issue-tracker.md) and
[roadmap](../../../docs/roadmap.md). Check named private plans if supplied.

1. Look for a task already underway in the current branch or an existing PR
   for the selected issue. Recover its actual state, including CI and any
   unaddressed review, before proposing another.
2. Rank open, unblocked issues by maintainer priority, scope clarity, and
   whether they unblock approved roadmap work. Inspect the issue body,
   dependencies, assignee, related PRs, and current code for the shortlist.
3. Present the recommended issue and why it is actionable. Distinguish
   missing information, active ownership, and external prerequisites. If
   work is already selected and authorized, hand off directly to
   [implement-printstash](../implement-printstash/SKILL.md). Assignment or
   issue edits follow the tracker guide and the user's authorization.
