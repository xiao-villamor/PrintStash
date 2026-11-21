---
name: deliver-printstash
description: >
  Commit and push task-owned PrintStash changes, open or update their pull request, and verify required CI.
---

# Deliver PrintStash

Follow the [branch, commit, PR, and changelog conventions](references/conventions.md).
[AGENTS.md](../../../AGENTS.md#task-completion) defines completion. This
workflow delivers through an open PR. Merging requires separate authorization.

1. Inspect the worktree and the declared base. Start new work from current
   `origin/main`; continue the existing task branch for work already underway.
   Keep unrelated changes out of the staged diff.
2. Check the final diff and run [applicable verification](../run-tests/SKILL.md).
   Keep user-facing release notes under Unreleased. Commit using the
   repository's configured identity.
3. Push the branch to a writable remote. Create or update its single PR
   against upstream `main`, using [.github/PULL_REQUEST_TEMPLATE.md](../../../.github/PULL_REQUEST_TEMPLATE.md).
   Use `Closes #N` for a fully delivered issue and `Refs #N` for partial work.
   Include behavior, test outcomes, and the coverage matrix where applicable.
4. Confirm the remote head matches the delivered commit. Verify `PR gate`
   succeeds for that head and resolve failed required checks. For a fork PR
   with an Actions approval requirement, report which run awaits a maintainer.
   If backend changes require Deep CI, verify its backend partitions and
   combined coverage gate for the same head per [run-tests](../run-tests/SKILL.md).

Report the PR URL, head SHA, and verified check result. Keep private evidence
under `reports/`.
