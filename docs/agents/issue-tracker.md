# Issue tracker

Use GitHub Issues in `xiao-villamor/PrintStash`. Pass
`-R xiao-villamor/PrintStash` to `gh issue` commands so work from a fork targets
the upstream tracker. Read issue bodies, comments, labels, assignees, and
dependencies before treating a ticket as ready. Verify collaborator access
before editing an issue.

- Read with `gh issue view <number> --comments -R xiao-villamor/PrintStash`.
- Search with `gh issue list --search '<terms>' -R xiao-villamor/PrintStash`.
- For authorized creation or editing, pass the reviewed text as a body file.
  Use the existing [triage labels](triage-labels.md). Preserve unrelated fields
  and history.
- GitHub's native issue dependencies and sub-issues record blocking and parent
  relationships. Where dependencies are unavailable, link blockers in a
  **Blocked by** section and check their current state before pickup. A roadmap
  theme, shared label, or parent issue alone does not block implementation.
- Keep the agreed outcome and acceptance criteria in the issue. Keep execution
  notes in `reports/` and evidence in the PR. The
  [delivery workflow](../../.agents/skills/deliver-printstash/SKILL.md) governs
  closing references. The issue stays open until the PR that closes it merges.

**PRs as a request surface: no.** Triage operates on GitHub Issues. Review PRs
with [review-printstash](../../.agents/skills/review-printstash/SKILL.md).

## Wayfinding

For an explicitly requested wayfinding session, use one map issue with linked
child issues for its questions and experiments. Write each agreed outcome to the
map with a link to its evidence. List actions in blocking order. Get maintainer
approval before adding any missing wayfinder-specific labels.
