---
name: settle-printstash
description: >
  Capture an agreed PrintStash term, decision, engineering convention, or workflow lesson, or reconcile guidance with a delivered change.
---

# Settle PrintStash

At a natural checkpoint, identify what should survive this session. Keep
session history, measurements, and review notes under `reports/`. Verify the
proposed durable wording against current code and decisions.

| Discovery | Owning home |
| --- | --- |
| Domain term or relationship | [CONTEXT.md](../../../CONTEXT.md), with [domain-modeling](../domain-modeling/SKILL.md) |
| Product or operator behavior | The owning maintained guide from [docs/README.md](../../../docs/README.md) |
| Architectural trade-off | The existing decision record or owning architecture guide. Use [domain guidance](../../../docs/agents/domain.md) for a new record. |
| Always-applicable scoped rule | The narrowest applicable `AGENTS.md` |
| Task-specific practice or pitfall | The local skill that owns the procedure, or its reference |

Prefer correcting the paragraph the change invalidated. Check
[skills-lock.json](../../../skills-lock.json) before targeting skills and use
[writing-for-agents](../writing-for-agents/SKILL.md) for local guidance edits.
File upstream proposals separately. Confirm new material decisions and new
public docs before writing. Existing authorization covers already agreed
changes.

Check that paths and commands resolve, terms match the glossary, and the guide
describes the behavior delivered. Leave each durable rule in one owning home.

## Harvest, route, and apply

1. **Harvest one fact per note.** Separate a domain term, product claim,
   implementation map, convention, rationale, procedure, and pitfall even
   when they came from one change. Keep evidence with each note.
2. **Keep what changes future work.** Retain a durable fact a later session
   needs or a rule that changes an observable decision. Leave historical
   conversation, benchmarks, and incident details in the private work
   record. Record a specific trigger and response for a recurring pitfall.
3. **Find the existing owner.** Inspect the current scope guidance, skill
   descriptions, glossary, and maintained guides. Replace stale wording
   at its source and link from consumers. If two rules claim the same
   responsibility, reconcile the overlap.
4. **Resolve scope before writing.** Show proposed destinations and
   material new choices when they require user decisions. Apply the
   already-authorized edits directly. Record architectural rationale when
   the choice is hard to reverse, surprising without context, and has a
   genuine alternative. Use the existing decision home where possible.
5. **Verify the change.** Check the rationale has alternatives, the map
   follows current code, the procedure refers to real commands, and the
   term has a single canonical definition. Check a skill trigger against
   representative tasks and nearby cases it should leave to other skills.

## Drift to check before delivery

| Changed | Check |
| --- | --- |
| Capability, dependency direction, composition, or ownership | Architecture map, owning skill, affected interfaces and tests |
| Schema, migration, read/write contract, or event | Compatibility guidance, API snapshot, generated definitions, supported behavior docs |
| Route, interaction, or design primitive | Owning feature guidance, preview, and DESIGN where its contract changes |
| Commands, requirements, test lanes, or CI | Executable command or workflow, running-tests and delivery docs |
| Provider support or limitations | Capability claims, compatibility evidence, support guide and changelog |
| Skill discovery or authorship | Scope routing, file links, invocation metadata, and lockfile provenance |

## Decide whether a design needs a record

Inspect the affected rationale when changing a supported storage engine,
persistence ownership, a durable Job boundary, authorization guarantees,
or a public compatibility contract. The same applies to package or
deployment dependencies that change what a default installation needs.
For each, determine whether the work follows the existing decision,
refines it, or changes it. An implementation detail can be explained at
the owning seam.

When a new durable decision is warranted, record the problem, constraints,
credible alternatives, chosen approach, and consequences. Link the
governing behavior or migration. Keep past rationale recoverable if the
decision changes. Use tracked docs only for maintained guidance and
publish a new record only at the agreed path.
