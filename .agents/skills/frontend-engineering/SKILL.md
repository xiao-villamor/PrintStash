---
name: frontend-engineering
description: >
  Plan, implement, or review PrintStash React UI, routing, interaction state, localization, design tokens, and prototype fidelity.
---

# Frontend engineering

Read [DESIGN.md](../../../DESIGN.md) and the [frontend reference](references/frontend.md).
For a supplied prototype or reported visual mismatch, read
[prototype fidelity](references/frontend.md#prototype-fidelity) before editing.

1. Find the route, feature entry point, and existing UI primitives. Keep
   screen-specific presentation with its route and reusable primitives in their
   existing shared owner. Check the dependency boundaries before moving code.
2. Identify each state the user can reach, including partial data, empty
   results, pending actions, errors, and recovery. Keep interaction state with
   the component or feature that owns its lifetime. For server-backed state or
   writes follow [frontend-backend integration](../frontend-backend-integration/SKILL.md).
3. Compose the design-system primitives and semantic tokens. Preserve keyboard
   behavior, focus, localization, and the prescribed motion scale.
4. Use [create-tests](../create-tests/SKILL.md) and [run-tests](../run-tests/SKILL.md)
   for the affected behavior. For visual or motion changes use
   [ui-capture](../ui-capture/SKILL.md) and the
   [browser review rules](../create-tests/references/tests/playwright.md#viewport-and-visual-review).

Completion includes evidence from the actual preview for the changed states
and interactions. Where a control writes data, verify the visible receipt and
persistence after reload.
