# View ownership and navigation

## State follows identity and lifetime

Prefer stable, canonical item IDs for route params, React keys, selection,
and anchor restoration. Array positions, labels, and joined display strings
change as items reorder or are localized. Keep parameters typed and let
the destination resolve current server state in its own scope. Route or
shell state owns URL, pane, and navigation decisions. Embedded components
receive a narrow action or navigation input rather than reaching into
the shell.

A transient input may belong to one row or editor. Lift drafts, selection,
or accepted work to the nearest owner that outlives unmount or virtualization
when the behavior requires it. Keep disposable state local. A list item
changing position should retain identity. A different target, session, or
scope should reset state that was tied to the previous one.

Put a dialog or menu at the owner of its trigger and target. Use the
existing primitives for keyboard behavior, anchors, focus restoration,
and dismissal. Changing data can remove a target mid-flow. Define where
focus and navigation land when the target is gone and preserve the draft
when removal was not confirmed.

## Collections and performance

Use the existing collection or navigation owner for search, cursor paging,
filtering, selection, visibility admission, and restoration. Retain a
coherent Library presentation while replacement data loads only where the
authority checks permit it. Give an empty collection, a filtered empty
result, and an unavailable read their intended actions.

Bound costly work by what is visible or actionable. Use the existing
realization, media admission, or worker path for expensive rows, meshes, and
previews. Use eager rendering for small controls when their relative geometry
requires the full group. A visibility optimization can evict children. Keep
state that must survive eviction out of the disposable subtree.

Use CSS layout and intrinsic sizing first. When measurement is necessary,
measure at the stable geometry owner, update only for meaningful changes,
and avoid making observation cause the size it observes to oscillate.
Check long localized text, focus outlines, content arriving after layout,
and preserved scroll position. Verify motion through
[UI capture](../../ui-capture/SKILL.md) and use the established performance
harness when the change affects large collections or render cost.
