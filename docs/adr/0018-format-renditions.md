# ADR-0018: Produce other formats as Renditions, never as a second source of truth

Status: Proposed — design for #536; new source formats tracked in #537 (PLY),
#541 (glTF/GLB) and #542 (IGES, BREP).

## Context

A Model can already hold several Artifacts in different formats: an author's
STEP, 3MF and STL sit side by side as independent uploads. What PrintStash
cannot do is give a user a format they did not upload. The next tool in a
printing workflow often needs one: Bambu Studio loads only 3MF from a URL,
mesh-only tools need STL or OBJ, and PLY scans (#537), glTF assets (#541) and
IGES or BREP CAD parts (#542) open in few slicers or none.

Conversion is directional and usually lossy. STEP → mesh approximates exact
surfaces; 3MF → STL drops objects, colours, plates and settings; mesh → STEP
produces a faceted solid that looks editable and is not. The design has to make
that fidelity visible and keep the converted file from being mistaken for an
upload.

## Decision

1. **A Rendition is a derivative.** A Rendition is a file in another format
   produced from one Artifact by a versioned conversion recipe. It is a
   derivative kind per target format (`rendition_stl`, `rendition_3mf`,
   `rendition_obj`) in `artifact_derivatives`, produced by a Job Definition on
   `derive.native`. It is not a `File` row. `viewer_stl` stays a separate,
   internal kind: its contract follows viewer needs (ADR-0014), a Rendition's
   follows the downloaded file.
2. **Uploads stay first-class and win.** A Model's uploaded Artifact in the
   requested format is always offered instead of a conversion. There is no
   override mechanism, so no file is ever both derived and authoritative.
3. **One mechanism, three policies.** An administrator sets one policy per
   target format: `always` (produced in the background and never evicted),
   `on_demand` (the default: produced on first request, evicted after a period
   without use or when over a size budget, recreated transparently) or `off`.
   The policy decides only when a Rendition is produced and how long it is kept,
   never what it is.
4. **Keep as Artifact.** A user can promote a Rendition to an Artifact through
   `persist_artifact`, with provenance naming the source Artifact and recipe.
   The result is an ordinary upload with the full Artifact lifecycle and is no
   longer tied to the source.
5. **Fidelity and conformance are closed classifications.** Every matrix cell is
   `exact`, `attributes_dropped`, `approximated` or `refused`. Every produced
   Rendition is also `conforming` or `non_conforming` to its target format's
   rules (see "Mesh conformance"). The Rendition records both, what it dropped
   and any tolerance, and the UI shows them.
6. **Refuse instead of fabricating.** Conversions with no faithful result are not
   offered: mesh → STEP, DXF → anything, mesh ↔ G-code (slicing is not
   conversion). Requests for them fail validation; they never enqueue work.
7. **Never repair, never rescale.** Only lossless fixes are applied: welding
   exactly equal vertices and flipping a surface whose winding is consistent but
   inverted. Hole filling, nearby-vertex merging and unit guesses change the
   geometry, so they are never applied silently. A suspicious scale produces a
   hint; a scale the user chooses is a separate recipe.
8. **Conversion settings are library-wide, never prompted.** Renditions are
   cached, shared between users and produced in the background, so nothing asks
   the user for settings at conversion time. The one setting with real
   trade-offs, STEP quality, is a bounded set of presets (see "STEP quality").

## Conversion matrix

Rows are sources and columns are targets. "—" means same format: nothing to do.

| Source ↓ / Target → | STL | 3MF | OBJ | STEP |
|---|---|---|---|---|
| **STL** | — | exact¹ | exact | refused |
| **3MF** | attributes dropped² | — | attributes dropped² | refused |
| **OBJ** | attributes dropped³ | attributes dropped³ | — | refused |
| **STEP** | approximated⁴ | approximated⁴ | approximated⁴ | — |
| **PLY** (#537) | attributes dropped⁵ | attributes dropped⁵ | attributes dropped⁵ | refused |
| **DXF** | refused⁶ | refused⁶ | refused⁶ | refused⁶ |
| **G-code / bgcode** | refused⁷ | refused⁷ | refused⁷ | refused⁷ |
| **glTF / GLB** (#541) | attributes dropped⁸ | attributes dropped⁸ | attributes dropped⁸ | refused |
| **IGES** (#542) | approximated⁹ | approximated⁹ | approximated⁹ | not offered¹⁰ |
| **BREP** (#542) | approximated⁹ | approximated⁹ | approximated⁹ | not offered¹⁰ |
| **Design sources** (SCAD, FCStd, f3d, blend) | refused¹¹ | refused¹¹ | refused¹¹ | refused¹¹ |

1. STL declares no units. The 3MF states millimetres, the same assumption
   PrintStash already makes for STL metadata. Coordinates are copied from
   float32 without loss. STL shares no vertices between triangles, so exactly
   equal vertices are welded to give the indexed mesh 3MF requires. An open or
   non-manifold STL still converts exactly but is `non_conforming`.
2. Build items and components are flattened into world coordinates and merged
   into one mesh. Object names survive as OBJ groups. Colours, paint, materials,
   modifiers, plates, print settings and embedded thumbnails are dropped and
   listed. 3MF coordinates rounded to STL float32 are reported as precision loss.
3. Polygons are triangulated. Non-planar faces with more than three vertices are
   ambiguous, so the Rendition is reported as `approximated` when it has any.
   UVs, normals and MTL materials are dropped. Groups become 3MF objects. A
   later 3MF recipe can keep MTL colours (see footnote 5).
4. Tessellated with OCP at the library's STEP quality preset (see "STEP
   quality") and converted to millimetres from the units the STEP file declares.
   This is a separate tessellation from metadata and preview, which keep their
   coarser preview recipe; the Rendition is the mesh meant for slicing. Faces
   with reversed orientation have their triangles flipped, as the slicers'
   own STEP importers do. Named solids become named 3MF objects; for STL they
   are merged.
5. Faces are kept and vertex colours dropped. A face-less PLY (point cloud) is
   rejected at ingestion and never reaches this matrix. A later 3MF recipe can
   keep colour by writing per-triangle `m:colorgroup` colours from the
   Materials extension: Bambu Studio maps standard 3MF colour groups to the
   loaded filaments on import. PrintStash does not reduce colours to a filament
   count itself, because only the slicer knows which filaments are loaded.
6. A 2D drawing needs an extrusion the user has to choose; that is modelling,
   not conversion.
7. Mesh → G-code is slicing and needs printer, filament and profile choices.
   G-code → mesh is not recoverable. Lossless bgcode ↔ gcode repackaging is a
   possible later row, in its own class.
8. glTF is defined in metres with +Y up (glTF 2.0 §3.4). Geometry is scaled by
   1000 and rotated +90° about X, so it is in millimetres with +Z up. Both
   conversions come from the specification, not from a scale hint. Node
   transforms are applied; 3MF keeps nodes as named objects, STL merges them.
   Triangle strips and fans are triangulated losslessly. Point and line
   primitives are skipped, and a file with no triangles is refused. The mesh is
   taken in its default static pose. Materials, textures, animation, skins and
   morph targets are dropped and listed. Draco- or meshopt-compressed geometry
   is refused until a decoder is admitted. `COLOR_0` follows the same
   colour-preserving 3MF follow-up as PLY (footnote 5). Coordinates scaled and
   then rounded to STL float32 are reported as precision loss.
9. Read with OCP (`IGESControl`, `BRepTools`) and tessellated exactly as STEP
   (footnote 4), at the same quality presets. IGES declares its units in its
   global section and they are converted exactly. BREP declares none, so it is
   assumed to be in millimetres (OCCT's convention) and the scale hint applies.
   IGES often holds trimmed surfaces that were never sewn into a solid. Those
   tessellate to open shells and are `non_conforming`. Sewing uses a tolerance
   and changes topology, so it is not applied (decision 7).
10. Translating IGES or BREP to STEP is a faithful B-rep → B-rep translation
    through OCCT, unlike mesh → STEP, so it is not `refused`. It is simply not
    offered, because no printing workflow needs it yet. Adding it later is a
    new matrix cell, not a change of policy.
11. A design source (OpenSCAD, FreeCAD, Fusion, Blender project) produces a
    mesh only by running its authoring application. For OpenSCAD that means
    executing the file's code; for the others it needs proprietary or
    heavyweight applications. PrintStash stores design sources but never
    converts them.

PLY, glTF, IGES and BREP are not targets: no current printing workflow needs
them.

Writers must run on every supported platform. lib3mf is installed only on
x86_64, so the 3MF writer must not depend on it. trimesh's `export_3MF` is pure
Python, streams vertices and triangles in batches, and writes components and
build transforms, so PrintStash uses it.

Its output is not byte-identical between runs, because it writes random UUIDs
and current zip timestamps. That is acceptable. A Rendition only changes bytes
when it is regenerated after eviction or a recipe bump, and then it gets a new
representation validator. The Rendition hash ledger records every hash ever
produced rather than assuming one per recipe. Tests compare parsed geometry and
metadata, not bytes.

trimesh writes no `<metadata>` elements. In-file 3MF attribution (see
"Attribution") therefore follows once `export_3MF` accepts metadata, ideally
through an upstream trimesh contribution rather than a PrintStash fork of the
writer. Until then, attribution lives in the UI and the derivative record.

## STEP quality

B-rep sources (STEP, and IGES and BREP from #542) are where the user faces a
real trade-off: finer tessellation gives smoother curves, but more triangles
and larger files. The
preview recipe (0.05 mm) is 10–17× coarser than the slicers' own STEP imports,
so Renditions use slicer-grade presets with PrusaSlicer's values:

| Preset | Linear deflection | Angular deflection |
|---|---|---|
| Low | 0.005 mm | 1.0 rad |
| Medium (default) | 0.003 mm | 0.5 rad |
| High | 0.001 mm | 0.25 rad |

- An administrator picks the library preset in Settings. The `always` policy,
  Model-level downloads and Open in slicer use it.
- "Download as…" offers a per-request Low / Medium / High override for B-rep
  sources (STEP, IGES, BREP). Each preset is a separately cached Rendition, and the preset is part
  of its identity. There are at most three variants per Artifact and target, so
  the cache stays bounded.
- There are no free-form tolerances. They would make the number of cached
  variants unbounded for little practical gain. They can be added later if
  asked for.
- Each Rendition shows the triangle count and size it actually produced. Other
  presets show "not generated yet". Unlike Bambu Studio's live import dialog,
  PrintStash cannot know a count without doing the tessellation.

Even Low is well below the accuracy of FDM and resin printers. The presets
exist for file size (a large assembly at Low) and for precision parts (High),
not to make an ordinary print acceptable.

## Mesh conformance

3MF requires meshes of printable objects (`model`, `solidsupport`) to have
manifold edges defined by vertex index, consistent winding and outward normals.
STL and many OBJ and PLY files (scans especially) do not meet this. Slicers
repair meshes on import anyway: PrusaSlicer matches near vertices, fills holes
and fixes normals on every load.

PrintStash applies only the lossless fixes from decision 7, then checks the
result. A mesh that still fails is written as it is and marked
`non_conforming`. The UI says "This mesh is not watertight; slicers will repair
it when it is imported". It is never written as a `surface` object, which
slicers would not print as a solid, and it is never repaired, because repairing
changes geometry by an amount that cannot be stated as a tolerance.

## Scale hints

Unitless sources (STL, OBJ, PLY, BREP) are assumed to be in millimetres. Bambu Studio
silently rescales small imports, treating anything under 0.008 mm³ as metres
and under 8 mm³ as inches. PrintStash uses the same thresholds only to show a
hint on the Model ("This looks like it may be in metres"). Choosing a scale
from the hint creates a Rendition at that scale, which is a separate recipe
recorded in its attribution. Nothing is rescaled without that choice. This
matters most for photogrammetry PLY (#537).

## Choosing a source

A Model can hold several uploaded Artifacts, and the source changes what a
Rendition can carry. A request on one Artifact converts that Artifact. A
Model-level request, Open in slicer and the `always` policy choose one
**conversion source** per Model and target:

1. **An upload in the target format wins.** Use the Model's live uploaded
   Artifact in that format and convert nothing.
2. **A pinned source wins next.** A user can pin one of the Model's mesh
   Artifacts as its conversion source. This is for the case the ranking cannot
   see, for example an author's print-ready 3MF whose orientation matters more
   than the STEP's exactness. The pin is cleared when that Artifact is trashed.
3. **Otherwise use the source that carries the most information.** Rank formats
   by what they hold, not by the fidelity class of one conversion step:
   - STEP: exact surfaces, declared units, names;
   - BREP: exact solids but no units;
   - IGES: exact geometry, often as unsewn surfaces;
   - 3MF: declared units, objects and placements;
   - glTF: declared units and a scene graph, but no print semantics;
   - OBJ, then PLY;
   - STL last: no units, one shell.

   Design sources are never conversion sources (footnote 11).
4. **Within a format, use the latest live version.** Ties at the same version go
   to the most recently uploaded Artifact.

The choice is computed whenever it is needed, never stored as a separate
decision. Each Rendition belongs to one source Artifact version, because a
derivative row is keyed by `file_id`, so the Rendition itself records which
source it came from. Users can still convert any other Artifact on request.

### When the source changes

Uploading, trashing, restoring or pinning can change the conversion source. A
Rendition made from an Artifact that is no longer the conversion source is
**superseded**:

- Model-level requests and Open in slicer stop serving it straight away.
- Under `always`, the source query finds the Model missing its Rendition from
  the new source and produces one at interactive priority. The superseded
  Rendition keeps being shown until the new one is ready, as other derivative
  outputs already are.
- Under `on_demand`, the next request produces it from the new source.
- The superseded Rendition can be evicted at once, whatever its last access.
  Until then it still serves explicit requests on its own Artifact.

An upload in the target format supersedes every Rendition of that target on the
Model (rule 1), so the user's own file always replaces a converted one.

## Attribution

A Rendition names the **specific Artifact** it was made from, not only its
format. A Model can hold two STEP files, or several versions of one, so the
format alone is ambiguous. The derivative record and the UI carry:

- the source's original filename, version and SHA-256, plus a link to that
  Artifact;
- the source format, the fidelity and conformance classes, what was dropped,
  and the tolerance or preset where there is one;
- any user-chosen scale;
- the triangle count and size;
- the recipe identity, and why this source was chosen (`target_upload`,
  `pinned`, `ranked`, `requested`).

The UI shows this as "Converted from `bracket.step` · v3 · approximated at
0.003 mm (Medium)". The downloaded file is named after the source (`bracket.step` →
`bracket.3mf`), and the file records where it came from wherever the format
allows:

- **3MF** (follow-up, once trimesh writes metadata; see "Conversion matrix"): core-specification metadata. `Application` names PrintStash. `Title`,
  `Designer`, `Description`, `Copyright` and `LicenseTerms` come from the Model
  and its import provenance when known, so a scan or download from a model site
  keeps its author and licence when shared. Bambu Studio reads `Designer`,
  `Copyright` and `Application`. For the licence it uses its own unprefixed
  `License` name, which the core specification does not allow, so PrintStash
  writes only the conforming `LicenseTerms`.
- **OBJ:** a leading comment block with the same facts.
- **STL:** the 80-byte header carries a short source note. It must never begin
  with `solid`, which marks ASCII STL and would break readers, including
  PrintStash's own density estimator.

Provenance is copied, never invented. A field the Model does not record is
left out rather than filled with a placeholder.

## Integration

| Subsystem | Behaviour |
|---|---|
| Model identity, ingestion dedup | A Rendition never contributes. Every SHA-256 it is ever produced with goes into a durable ledger that eviction does not touch; regeneration adds a hash rather than replacing one. Re-uploading a downloaded Rendition attaches it to its source Model as an Artifact instead of creating a new Model. |
| Versions, new uploads | A Rendition belongs to one Artifact version. A new version, or a new Artifact that becomes the conversion source, supersedes it (see "When the source changes"). |
| Recipe bump | The derivative source finds Renditions missing at the new recipe. `always` targets are reconverted at backfill priority, `on_demand` ones on their next request. |
| Trash | A Rendition follows its source Artifact or Model into and out of the trash and is deleted with it. |
| Backups, library transfer | Excluded, because they can be regenerated. Restore brings them back through the policy. |
| GC, storage ownership | Rendition bytes use ownership intents like every derivative output. Eviction deletes only keys it owns and never walks storage. |
| Similarity, AI search | Excluded. A Rendition would match its own source and adds no text. |
| Revisions | Not applicable. A Rendition is never G-code. |
| Metadata, thumbnails | Not derived again. A Rendition shows its source's metadata and preview. |
| Downloads | Served through the existing download contract: current access check, `private` caching, its own representation validator (renewed when the Rendition is regenerated), and API delivery as for generated previews. |
| Open in slicer | A slicer that opens the source format receives the source. OrcaSlicer and PrusaSlicer open STEP themselves and apply their own import quality. Only when the slicer cannot open the source does the handoff serve a Rendition at the library preset (Bambu Studio → 3MF). A Rendition that is still pending shows as pending, not as an error. |
| Printers | Unchanged. Printers receive only G-code. |
| Public shares | Serve the original only, at first. |
| Vault stats | Report a separate "Converted files" size, so cached bytes are not counted as library growth. |

## Alternatives considered

- **Rendition as a `File` row with an `origin` column.** Rejected. Every
  Artifact consumer listed above would need an origin filter. One missing filter
  creates a duplicate Model, a self-match or a backup full of regenerable bytes.
- **One-time export behind an expiring link.** Rejected as the main mechanism.
  It repeats expensive conversions on every download, and an expiring link only
  helps sharing outside PrintStash. That belongs to a share-link feature.
- **Pre-generating every format.** Rejected as the default. It roughly doubles
  stored bytes and turns every recipe bump into a library-wide conversion. It
  remains available per target as the `always` policy.
- **Uploads overriding derivatives.** Not needed once resolution prefers
  uploads (decision 2).
- **Asking for settings at conversion time, as slicer import dialogs do.**
  Rejected. A slicer converts once for the person in front of it. A Rendition
  is cached, shared, and may be produced with nobody present. Presets
  (decision 8) give the same control without blocking one-click actions.
- **Repairing meshes so every 3MF conforms.** Rejected. Repair changes geometry
  by an amount that cannot be stated, and slicers repair on import anyway.
- **Recording conversions as linked files, as Manyfold does.** Manyfold, a
  self-hosted 3D print library, converts through assimp into a new permanent
  file related to its source as an `alternative_format_of`, and analyses it like
  any upload. This is the first alternative above, and it carries the same
  costs.

## Consequences

- `CONTEXT.md` gains **Rendition** when this is implemented (avoid: export, which
  already means library export; derived Artifact; copy).
- A new demand record per Artifact, target and variant (STEP preset or chosen
  scale) holds the request time and last access, which on-demand production
  and eviction need. The derivative key must likewise include the variant.
- Settings hold the per-target policy, the eviction budget and the STEP
  quality preset.
- Promoting an ASCII STL's 3MF Rendition with Keep as Artifact, then trashing
  the original, saves storage. This is always the user's explicit choice and is
  never automated.
- A Model gains an optional pinned conversion source, an Artifact of that Model.
- Conversion is bounded by the same native admission and mesh-density limits as
  metadata. A source over the ceiling is refused with `geometry_work_limit`, so
  the densest meshes cannot be converted until those limits change.
- Each matrix cell has its own recipe, so a change to one writer reconverts only
  the Renditions of that cell.
