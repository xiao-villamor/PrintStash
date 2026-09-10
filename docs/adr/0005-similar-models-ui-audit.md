# Similar Models interface audit

Scope: standalone queue, comparison, Maintenance controls and local semantic
search. Reviewed against `DESIGN.md` and the Impeccable audit/polish guidance.
This is development evidence for the feature branch, not a released capability.

The rendered review covered 1280×800 desktop and 390×844 mobile viewports.
Maintenance puts enablement and upload analysis together, keeps the confidence
control compact and collapses numeric budgets. Scope controls wrap on mobile.
Queue rows use compact previews and keep Compare reachable. Comparison actions
precede the viewers and measurements, with two columns on desktop and stacked
viewers on mobile. Long Model names wrap without widening the page. Multipart confirmation limits
each part label to two lines while retaining the full accessible text and native
title, keeping quantities and both actions visible at 390px without scrolling.

The real-backend browser scenario checked both viewport widths for horizontal
overflow, rotated one comparison viewer, compared the rendered silhouettes of
both viewers, confirmed evidence and reloaded the saved decision. Model names,
Artifact hashes and G-code membership remained unchanged. Screenshot comparison
hides the development-tools button so it cannot obscure only one canvas; it
allows rasterization differences but requires over 98% silhouette overlap.

The one Impeccable detector pass reported no findings across the five new UI
components. Unit tests cover Spanish review labels, stale actions, missing
previews, settings scopes, threshold-preview errors and explicit existing
Multipart selection. Existing Modal, Button, Checkbox and token
styles supply the interaction primitives.

Remaining acceptance work is recorded in the [coverage matrix](0005-similar-models-coverage.md).
This audit does not claim an exhaustive accessibility certification or hardware
rendering certification. The browser scenario passed with the existing Calibration Cube STL and its real
G-code. A second browser scenario uses a 2x Cube and a reflected, sheared copy
to verify compensation and overlay against actual WebGL pixels. These display
fixtures do not serve as classifier-quality evidence.

The final real-backend run passed using the Cube and G-code at both viewport
sizes, including the persisted confirmation. All three WebGL/layout tests passed;
a second focused layout run confirmed that the compact long-name dialog stays
below 650px tall with visible actions. Wrong scale/reflection compensation still
fails the silhouette comparison; the correct transform exceeds 98% overlap with
at most a one-pixel raster alignment allowance. Concurrent review conflicts now
close the old dialog, refresh the evidence and require a new explicit review.
Evidence confirmation uses a compact primary-action dialog, without a destructive
warning icon. The comparison page's 22 interaction tests pass.
