# Shared bounded STL reader

Measurements, full mesh loading, retained fallback samples and the two-pass
streaming preview use `stl_reader.iter_stl_blocks`. Consumers choose what to
retain or render; they share the interpretation of source facets, limits and
source identity. No successful completion is reported from a partially validated
source.

## Source validation

An exact binary STL has an 84-byte header followed by its declared 50-byte facet
records. A binary header can begin with `solid`; record length still identifies
it. Trailing or truncated records are refused. Binary stored normals are ignored
because geometry normals are derived from vertices. Nonfinite vertex coordinates
are refused.

ASCII vertices remain float64. Blank lines and comments are accepted; a complete
facet boundary at EOF is accepted without `endsolid`. Incomplete facets,
nonfinite or overflowing coordinates, malformed normal tokens, and content after
`endsolid` are refused. Byte, facet, line, line-length and deadline budgets apply
to the entire validated source, including facets outside a retained sample.

Every read is pinned by device, inode, size, modification time and change time.
Opening, EOF, later passes and final result publication verify that identity.
Replacing a source is refused even if its size and modification time are
restored. A preview worker that detects a change after rendering writes no
successful completion manifest; the parent accepts output only from a successful
worker with a valid manifest.

## Separate consumers and coverage

| Consumer | Retained representation | Completion contract |
| --- | --- | --- |
| Measurements | Exact source bounds and facet count | Every source facet validated to stable EOF; topology remains unassessed |
| Full mesh loading | Admitted float64 facet arrays | Binary materializes after its header probe; ASCII scans for allocation size then materializes under the same snapshot and deadline |
| Fallback sample | Deterministic bounded subset | `STLSample.source_complete` certifies source validation; `complete` certifies every source facet was retained |
| Sampled thumbnail | Bounded facets and image buffers | Source coverage is independent of sample/raster coverage; `STLThumbnailResult.complete` additionally requires full retention and remaining raster budget |
| Streaming preview | Bounded blocks and image buffers | Full bounds pass precedes rasterization of all facets; candidate budgets and the source snapshot must complete before publication |

The strict `read_stl_sample` API preserves invalid-source, resource-limit and
source-changed refusals. Legacy sample/preview adapters keep their established
`None` refusal result. Neither a complete read nor a complete preview certifies
watertight topology or calculable physical volume. Thumbnail outcomes translate
these facts into explicit source, representation and preview coverage.

## Sampling and framing

Retained facet indices use fixed vectorized priorities, preserve source order,
and include the first and last facet when the cap permits. The subset is
independent of reader block size and binary/ASCII encoding. Validation still
reads the full bounded source rather than seeking selected binary records.
Sampling keeps at most its retention budget plus one reader block as facet
candidates; it does not allocate a complete mesh.

Camera bounds use every validated source facet, including a small remote
component, rather than sampled percentiles. Translation is removed in float64
before visual projection. Streaming converts bounded screen coordinates and
scaled depth to float32. Facet degeneracy checks remain invariant under rigid
transforms. Existing camera, material, transparency and raster-candidate controls
remain in force: holes stay transparent and a sampled or raster-limited preview
is reported as partial. Temporary analysis geometry is released before fallback
rendering; image memory remains bounded by output dimensions.

## Refresh and cost

Shared STL validation introduced mesh metadata recipe11, thumbnail recipe10
and geometry interpretation `geometry-v6-sh5f4577c4`. Subsequent retained-scene
eligibility changes and [current recipe identities](derivatives.md#retained-3mf-measurements-and-previews)
are recorded with the derivative contracts. Viewer STL remains recipe2. Mathematical descriptors, the SH basis and
verifier
calibration are unchanged; historical evidence and user review decisions remain
retained. [3MF capabilities](3mf-capabilities.md) and independent embedded-preview
publication continue to apply to 3MF sources.

Complete source validation can read more bytes than selective record seeking.
This is a correctness change with bounded work, not a claim of improved runtime.
Pass reuse and retained scene consumers are separate implementation decisions.
