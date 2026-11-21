---
name: coordinate-printstash
description: >
  Coordinate PrintStash implementation or review when the user explicitly requests subagents. Assign ownership, integrate handbacks, and verify the final result.
---

# Coordinate PrintStash

The current thread owns decisions, integration, and delivery. Read
[AGENTS.md](../../../AGENTS.md#agent-coordination) first. Apply this skill only
when the current request explicitly authorizes subagents. It changes staffing
for [implement-printstash](../implement-printstash/SKILL.md) and
[review-printstash](../review-printstash/SKILL.md), while their completion
criteria still apply.

1. **Plan the split.** Use the approved plan to select the fewest useful
   workers. Assign one owner per file, schema, migration chain, registry, or
   generated contract. Define shared interfaces and acceptance evidence
   before either side starts. Sequence any work that needs the same unfinished
   build inputs. Use the harness's authorized tooling and user-selected models.
2. **Write brief and checkpoint.** Give each worker its objective, owned
   files, phase, contracts, applicable skills, expected checks, and handback
   format. Keep a private roster under `reports/` with handles, file ownership,
   statuses, and result SHAs or diffs. Keep the task's delivery branch and PR
   under coordinator ownership. Use separate review contexts only if authorized.
3. **Integrate.** Compare each handback to its brief. Resolve interface and
   test mismatches before starting dependent work. Run the checks that span
   workers on the integrated result. Review lanes work read-only against one
   pinned diff and return evidence under Standards or Spec.
4. **Close.** Apply task-owned fixes and re-review the affected contracts.
   Resolve user decisions in the coordinator thread. Settle docs and deliver
   through the single task PR when the full contract and checks are satisfied.

A worker reports changed files, acceptance evidence, tests, and blockers.
Coordinator verification determines the status of the integrated result.
