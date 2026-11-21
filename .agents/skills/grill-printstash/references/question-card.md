# Question card

Use one card per unresolved decision. Number cards across rounds so a reply
can point to one question even after the discussion has moved on.

**Q1. What should happen in this scenario?**

- **Context** identifies the caller and the concrete state that exposes the
  choice. Cite the current behavior and any settled constraint.
- **Options** name the meaningful alternatives and their consequences.
- **Recommendation** chooses one option and explains the trade-off.
- **Record** names the owning guide, terminology, or issue criterion that
  changes once the decision is made.

Present independent cards in one round. Hold dependent cards until earlier
answers give them concrete inputs. A request for more detail keeps the card
open. Update the next round from confirmed answers.

## Example

**Q1. Which edit should win when two people change the same Model label?**

- **Context** The label editor starts from a versioned snapshot. Another
  authorized session can rename the Model while the first draft is dirty.
- **Options** Require the first editor to review the new version, or accept
  its changed label against the latest Model automatically. The latter
  can overwrite a label the first editor never saw.
- **Recommendation** Preserve the first draft and show a stale edit
  conflict. The user can see both values before submitting again.
- **Record** Add the stale-edit outcome and test case to the selected
  issue's acceptance criteria. Confirm the implementation uses the
  existing conditional editing flow.
