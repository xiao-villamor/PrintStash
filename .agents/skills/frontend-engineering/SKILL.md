---
name: frontend-engineering
description: Plan, implement, or review PrintStash React UI, routing, view and draft ownership, localization, layout performance, design tokens, and prototype fidelity.
---

# Frontend engineering

Read [DESIGN.md](../../../DESIGN.md),
[implementation principles](../implementation-principles/SKILL.md), and the
[frontend reference](references/frontend.md). Use
[codebase architecture](../codebase-architecture/SKILL.md) to place a view,
feature interface, or shared primitive.

| When changing | Read |
| --- | --- |
| Routes, component decomposition, measurements, navigation, or collection state | [View ownership and navigation](references/views-navigation.md) |
| A form, wizard, inline editor, draft, or confirmation | [Drafts and feedback](references/drafts-feedback.md) |
| Query, mutation, event, export, session, or protected media | [Frontend-backend integration](../frontend-backend-integration/SKILL.md) |
| A supplied prototype or reported mismatch | [Prototype fidelity](references/frontend.md#prototype-fidelity) |
| A visual or motion change | [UI capture](../ui-capture/SKILL.md) and [browser review](../create-tests/references/tests/playwright.md#viewport-and-visual-review) |

## Frontend boundary

Routes and the shell own presentation and navigation. Feature modules own
application reads, writes, and stable interaction contracts. UI primitives
own shared appearance, keyboard semantics, and accessibility. Product
meaning stays with its feature even when several screens share a visual
structure. Keep feature data, auth state, and transport out of generic
primitives. Follow `frontend/scripts/dependency-boundaries.ts` before
drawing another public boundary.

The backend owns durable facts. TanStack Query owns server snapshots and
their freshness. Components own local focus, selection, navigation, and
draft inputs at the smallest stable lifetime that gives those states meaning.
Parent-supplied values remain current inputs. Copy them into state only
when creating an intentional draft or snapshot with an explicit reset rule.

## Build or extend a surface

1. **Read the surface and its neighbors.** Identify the production route,
   owning feature, current state transitions, and established primitive.
   Check the intended flow and acceptance evidence. Identify permission,
   empty, partial, pending, failure, and recovery states before styling.
2. **Place by semantic ownership.** Give an independently presented screen,
   panel, or complex editor a coherent home. Keep small structural children
   with their owner. Promote shared UI after real consumers need the same
   interaction and style contract. Scope local state to the control or
   region that owns its lifetime. Accepted durable work moves through the
   feature or Job boundary.
3. **Compose visible-first.** Render route-known heading, shell, and stable
   geometry while the data-dependent region is loading. Keep data and retry
   handling inside the region that can fail or become useful on its own.
   Preserve warm content where scope and freshness permit it. Show errors
   as errors and empty results only after a successful read.
4. **Compose design primitives.** Use the shared overlay, menu, form, and
   feedback paths from the design and frontend references. Use semantic tokens
   for colors, spacing, and motion. Verify keyboard order,
   dismissal, focus restoration, actionable labels, and permission feedback.
5. **Prove the production flow.** Verify loading, available and unavailable
   states through the existing React, Query, and router harness. Check the
   user-facing preview for changed visual states and real interactions.
   Confirm durable writes by the visible receipt and reload. Follow
   [create-tests](../create-tests/SKILL.md) and [run-tests](../run-tests/SKILL.md).

## Text, dates, and capability discovery

Use the existing localization path in `frontend/src/lib/i18n.tsx` and
`frontend/src/locales/`. Add the owning message, substitutions, and plural
variants for the supported catalogs. Use existing formatters for bytes,
durations, dates, counts, and units. Keep locale-dependent formatting local
to presentation and preserve typed facts from the backend. Give an
icon-only control an accessible name that describes its purpose.

Compose actions at the scope of the affected thing: a collection action in
its section or toolbar, a row action on its row, and a destructive entity
action through the existing confirmation primitive. Derive action
availability from the current scope and server-provided capabilities.
Use the same availability in menus, shortcuts, and selection actions.

## Completion

Verify stable route and row identity, focus, localized copy, shared
primitives, explicit failure states, and readable layouts under the supported
checks. For visual work report the actual preview states you exercised.
A screenshot can prove appearance at a moment. Interaction, motion and
persistence require their own evidence.
