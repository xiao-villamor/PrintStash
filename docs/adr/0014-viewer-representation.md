# ADR-0014: Qualify viewer representations before replacing STL

Status: Accepted — retain STL; do not automatically adopt the evaluated GLB/LOD candidates.

## Decision

Keep original Artifacts and the current on-demand STL derivative authoritative for the viewer. Retain bounded, isolated representation and simplification pilots with explicit quality failures. No application runtime dependency, derivative recipe, UI, metadata, similarity or original-download contract changes in this decision.

Indexed buffers and actual GPU instancing show useful savings in controlled cells. General replacement fails the evaluated correctness/resource contract, and automatic LOD lacks sufficient independent real-model and browser qualification. A future visual Derivative must have its own recipe and preserve the original geometry for metadata and similarity.

## Reconsideration conditions

- Encode the full admitted 3MF affine contract in valid GLB, including normals, reflection, precision and placement identity; reject unsupported visual outputs explicitly.
- Preserve actual instance sharing without a draw-call explosion, using a maintained instancing transform where its TRS contract applies.
- Demonstrate large real-model supervised export within a justified resource envelope and measure complete generation + publication + browser cost.
- Qualify LOD against independent real protected-feature, material and browser silhouette controls; expose attained counts and preserve measurements/similarity from reference geometry.
- Add a distinct durable visual Derivative recipe and additive viewer demand/status/selection contract only after those gates pass.

These conditions are scoped prerequisites for changing the default, not a deferred fix to the existing STL source contract. Retain CPU rendering and original downloads.
