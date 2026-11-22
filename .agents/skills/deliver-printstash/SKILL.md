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

## Preflight the actual delivery state

For a resumed task, compare the working tree, local HEAD, remote branch, and
PR head. Inspect existing review decisions and check evidence before
declaring any requirement complete. Confirm the declared base is current
enough for the check under review. If the base moved or conflicts appeared,
use the repository's branch conventions to sync, preserve unrelated work,
and rerun checks affected by the integration. Avoid rewriting published
history when extending an existing task.

Review the staged diff for unrelated content, private plans, and generated
outputs. Verify guide references, commands, and contract changes. For a
hard-to-reverse architecture decision, check that the owning documentation
records the rationale or link to the existing record through
[settle-printstash](../settle-printstash/SKILL.md).

## Make the PR's evidence usable

Describe the delivered result and why it matters. List the actual commands,
their outcomes, and relevant URLs. Use the test matrix to show what observable
assertions prove the behavior. Label gaps and external prerequisites
directly. For a versioned API or schema change call out compatibility and
upgrade behavior. Never publish private audit or plan contents.

Use the result of [run-tests](../run-tests/SKILL.md) to distinguish a failed
test, failed environment, known inherited failure, missing run, and pending
run. Fix task-owned failures before delivery. A successful run for an older
head is evidence for that commit only. If an external prerequisite prevents
the final gate, report the exact check and what remains.
