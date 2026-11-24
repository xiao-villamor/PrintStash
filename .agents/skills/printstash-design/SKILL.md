---
name: printstash-design
description: Refine existing PrintStash screens and interactions using Impeccable, Apple Human Interface Guidelines, and Mobbin references. Use for UI design, feedback on a rendered screen, and visual or accessibility reviews.
---

# PrintStash design review

Use [DESIGN.md](../../../DESIGN.md) for PrintStash typography, tokens, components, and motion. The [frontend reference](../printstash/references/frontend.md) covers integration and prototype fidelity. [UI capture](../ui-capture/SKILL.md) covers browser evidence.

## Choose the interaction before styling it

1. Open the actual screen with realistic data. Record the operator's task, the exact point where the current design gets in the way, and the functions the change must preserve. Capture the state the user identified, including an open menu or dialog if relevant.
2. Use the installed Impeccable skill. Run its context command for the target and apply its layout and clarify passes. Use polish and the mechanical detector for the final review where available. Keep the existing product identity for a scoped correction.
3. Use Mobbin for comparable screens and task flows. Read its best-practices and targeted redesign guidance, then inspect the reference images. Record the structural choices that help the operator and link the screens that show them. Build with PrintStash's own visual language and assets. Never copy a reference screen.
4. Use Apple HIG for familiar interaction behavior, clear labels, and the placement of secondary or destructive actions. Read the relevant guidance, such as [Buttons](https://developer.apple.com/design/human-interface-guidelines/buttons), [Menus](https://developer.apple.com/design/human-interface-guidelines/menus), or [Alerts](https://developer.apple.com/design/human-interface-guidelines/alerts). Adapt the behavior to PrintStash's web interface.

If a tool is unavailable, record the failure and continue with the available evidence. Distinguish a design decision from a reference observation.

## Review what ships

- Compare before and after at the same theme, viewport, and sidebar state. Inspect context around the control as well as its open state. For a reported visual fault, check the width where it occurred.
- Read the real labels, dates, notes, and counts. Check priority and alignment, truncation, empty and populated states, the open overlay's anchor, and whether actions compete with status or data.
- Measure contrast from rendered colors in both themes. Sample normal, hover, keyboard focus, and disabled or pending states as applicable. Check the actual label, its surface, and the visible focus indicator.
- Walk the action by pointer and keyboard. Verify dismissal and focus return, and confirm a write after reload using a disposable record. Review permission and failure states through the affected tests.
- Keep captures and measured findings in a private report. Link the report when requested. State which screens and states you actually exercised.
