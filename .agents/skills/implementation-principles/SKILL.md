---
name: implementation-principles
description: >
  Apply PrintStash implementation principles when planning, placing, or reviewing code, including ownership, module boundaries, state, errors, and scale.
---

# Implementation principles

Read [codebase-design](../codebase-design/SKILL.md) for deep modules and seam
design. PrintStash's [domain language](../../../CONTEXT.md) and
[repository rules](../../../AGENTS.md) govern local choices. Apply
[code principles](references/code-principles.md) to types, failures, and
library-scale operations.

## Choose the owner

1. Read the affected owner, its callers, and its dependencies. Name the rule
   each holds and which owner has enough information to enforce it.
2. Keep a cohesive owner or move behavior to the capability that already owns
   the rule. Split a responsibility when it has independent authority,
   lifecycle, or change pressure. Group code that changes together.
3. Choose a boundary proportional to the constraint. A file or folder can
   clarify responsibility. A port earns its cost when it protects a contract
   across real adapters. A package needs a dependency or distribution
   boundary. Check [repository topology](../codebase-architecture/SKILL.md).
4. Move affected consumers, composition, and tests with the owner. Preserve
   upgrade paths, public API and persisted identities within the agreed
   compatibility contract. Remove private forwarding paths once their
   consumers have migrated.

## Preserve truth across boundaries

- Model legal states and transitions explicitly. Expected absence, conflicting
  state, failed reads, and successful empty results have distinct meanings.
- Parse external representations at their adapter and carry structured values
  across the seam. Derive text for display from typed facts at the presentation
  boundary.
- Specify who can admit, cancel, and complete accepted work. Recheck authority
  and current state after waiting or crossing a transaction boundary. Give a
  durable effect an owner that can finish or recover after the caller exits.
- Keep irreversible effects behind the appropriate durable commit. Preserve
  enough evidence to reconcile a retry or an unknown outcome.

## Review the accumulated structure

Before review, revisit the whole affected owner. Look for duplicate rules,
forwarding layers, implicit lifetime dependencies, and assumptions that only
hold for small libraries. Fix task-owned issues at their source and identify
separate work for review. Verify the revised public entry points, dependent
consumers, and observable behavior through [create-tests](../create-tests/SKILL.md).
