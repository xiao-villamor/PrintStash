# Drafts and feedback

## Buffered editors

Treat a buffered control as an edit session. Name its target, initial
values, authority or edit base, draft, and pending outcome. Capture the
base when the draft starts and retain it while dirty. A background refresh
may replace a clean draft. A dirty draft facing changed authority or base
needs the established conflict or review path.

The owner validates the draft against the same constraints shown in the
controls. The server decides whether the write is still allowed. Prevent
duplicate submission while an equivalent operation is pending. Let explicit
cancel discard local input. On route changes or dismissal, follow the
feature's established discard semantics when edits would be lost.

On a confirmed write, apply the receipt and reset or navigate as intended.
If the write is rejected, preserve draft, focus, and selection for recovery.
If the response is lost, check the affected current state using the feature
path before proposing a new write. Preserve the original submission evidence
until the outcome is known.

## Feedback by outcome

| Outcome | Owning response |
| --- | --- |
| Read unavailable | Show recovery in the affected region through its retry or refetch. |
| Field invalid | Show bounded, localized validation feedback at the input. |
| Write refused or stale | Preserve the editor and use the feature's reviewed conflict or error path. |
| Unknown write outcome | Give explicit recovery through current server evidence. |
| Durable work admitted | Follow its Job status. The action and Job can complete at different times. |
| Navigation cancellation | Stop or disregard the abandoned read without overwriting a newer presentation. |

Use the existing error and feedback paths. Keep provider or transport
diagnostics out of copy. Optimistic display is appropriate only when the
owner has a way to reconcile authoritative results, failures, and later
writes. Use the established restore, undo, or compensation path where the
product offers it. A presentation-local closure cannot provide durable undo.
