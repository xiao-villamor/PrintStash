# Shared visual preparation

A visual pass prepares referenced geometry once and reuses it for its preview
and orthographic views. The shared core owns this representation and the
rasterizer. The application facade injects chunk budgets, logging and the raster
callback; embedding orchestration owns loading, admission and output selection.
No parser, storage, ORM or job engine dependency is introduced into core.

## Ownership and memory

`prepare_mesh_render` and `prepare_scene_render` return `PreparedRender` with
owned relative float32 positions, int64 welded position identities and float64
angle-weighted smooth normals. Source arrays remain untouched. Cached arrays and
emitted triangle chunks reject ordinary writes, and source mutations after
preparation do not change the prepared representation.

Scene preparation copies each unique resource's faces once and retains placement
ordering, offsets and reflected winding. It does not allocate a whole placed
float64 mesh or all placed faces. Expanded referenced positions, welded
identities and smooth normals are still held whole: this is not a constant-memory
renderer. Triangle temporaries and face traversal remain bounded by the configured
chunk size. Reuse is scoped to one pass, without a persistent cache or global
mutable state.

Each camera recomputes projection, culling and shading. Only camera-independent
welding and smooth normal accumulation are reused. Existing raster work budgets,
crease treatment, silhouette recovery, supersampling and vignette remain intact.

## Pixel and encoding contracts

`render_prepared_pixels` returns immutable final RGBA bytes with positive integer
dimensions and exact byte length. `RenderedPixels.rgb` requires an explicit
`RGBBackground`: `WHITE` preserves Pillow's white alpha composition for embedding
inputs; `IGNORE_ALPHA` preserves the grayscale conversion used by view hashes.
No PNG serialization or decoding is required by either consumer.

`render_prepared_thumbnail` uses the same pixel kernel and existing PNG/WebP
encoding settings. Original mesh/scene byte entry points prepare once and
delegate. The visual preview keeps WebP normalization, decoding and bicubic
resizing, including existing encoder identities. Point inputs are unchanged.
Missing render output remains an explicit refusal; missing decoded preview RGB
raises instead of supplying empty bytes.
