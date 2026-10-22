# Retained 3MF scenes

A 3MF scene keeps each reached mesh resource's vertices (float64 arrays) and
triangle indices separate from its instances. Each instance identifies a resource and an
affine placement. Repeated placements share source buffers instead of requiring
an immediate whole-scene mesh. Supported units are normalized to millimetres.

This preserves the [3MF capability policy](3mf-capabilities.md): unsupported
required extensions remain typed refusals. Original Artifact bytes and validated
embedded previews retain their independent lifetimes.

## Reading

The worker reads a package through lib3mf, the 3MF Consortium's reference
implementation, which parses into compact native buffers instead of an XML tree.
Its coordinates are single precision: a vertex holds the nearest float32 value
to its source text. Arithmetic on placements and measurements stays float64.

lib3mf only answers packages it reads with the XML reader's semantics. The XML
reader, in double precision, reads the package instead when any of these hold:

- a part requires an extension other than production, declares a DTD, or uses a
  unit the reader does not know;
- parts use different units (lib3mf scales every part by the root's unit);
- a production part lacks its own build element, which lib3mf refuses;
- a vertex or placement lies farther than 65,536 mm from the origin, where a
  float32 step exceeds 0.004 mm;
- lib3mf refuses the package for any other reason, or the platform has no
  lib3mf build (the published images ship it for x86-64 only).

Every refusal and its classification therefore stays the XML reader's. Running
out of memory inside lib3mf is the exception: it is a `resource_limit`, as it
would be for the heavier XML reader.

## Admission and consumer budgets

The bounded reader retains its hard ceiling of 2,000,000 faces. It validates both
reached source geometry and expanded placement counts, together with its existing
archive, XML, reference and transform controls. Retaining resources does not waive
these safety checks or authorize a later allocation.

Consumers then apply separate budgets:

- Basic bounds and triangle count use the retained scene without materializing a
  whole mesh. Placement arithmetic remains float64.
- Global topology resolution may materialize only within `load_face_budget`,
  which combines the configured triangle ceiling and the detected RAM budget.
  The RAM budget weighs each face by the reader that produced the scene:
  600 bytes for lib3mf, 3,600 bytes for the XML reader.
- Fingerprints use the request's analysis triangle cap. A scene above the basic
  materialization or render cap can still produce a fingerprint when it fits this
  analysis budget. An over-budget fingerprint is refused before materialization.
- Full preview rendering checks its configured and RAM-derived triangle cap.
  Refusing this output preserves independently available metadata, fingerprints
  and a validated document preview.

When topology and fingerprint extraction both need placed geometry, they reuse
one materialization during the request. Its temporary mesh buffers and measurement topology caches are released
before direct scene rendering, including when fingerprints are disabled. A failed topology attempt does not trigger a
second materialization for the fingerprint or erase known bounds and count.

Measurement retains unique resource geometry and referenced-vertex indices;
placed-coordinate scratch is limited to chunks of 64,000 points. This is not a
renderer memory guarantee. Visual preparation can expand referenced positions
for every placement, center them in float64, and create float32 render positions.
Triangle indices enter the rasterizer in bounded chunks.

## Physical volume and topology

Closed, consistently oriented resources use the canonical signed polyhedral
integral. The kernel keeps component-local origins, batches of 4,096 facets and
accurate summation. Instance contributions scale by the absolute determinant of
the placement; reflected facets retain the renderer's corrected winding policy.
Signed resource contributions remain signed until their final sum, so a
negatively oriented inner shell subtracts a cavity instead of adding its volume.
A nonfinite or nonpositive final integral is a typed unavailable volume.
When source and placement scales compensate for each other, intermediate
underflow or overflow must not reject a finite placed volume. The ordinary
signed integral/product remains unchanged when representable. Lost source
range is recovered with exact power-of-two coordinate scaling; signed
logarithmic placement arithmetic avoids an overflowing intermediate determinant.

Open resources require whole-scene topology evaluation: separate parts may close
only after global welding, and coincident unwelded copies may remain invalid.
Resources are not repaired independently to infer a valid global solid. Within
the topology budget, the materialized mesh uses the existing canonical volume
classification. Beyond that budget, or after failed materialization, exact bounds
and count remain usable with `TOPOLOGY_NOT_EVALUATED` volume. Other volume faults
retain their specific typed causes without inventing a scalar estimate.

## Coverage describes separate facts

`SourceScanState.COMPLETE` records a completed source read. It does not imply a
whole mesh was materialized, topology was established, or a preview was rendered.
A scene-only result therefore retains `GeometryNotLoaded`; successful topology or
fingerprint materialization records `CompleteGeometry`. Preview coverage records
its own outcome: complete rendered output, document-supplied output, or no output.
A supplied image cannot certify geometry that was refused or never read.

See [derivative contracts](derivatives.md) for publication and retry behavior.
