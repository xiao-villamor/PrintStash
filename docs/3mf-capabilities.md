# 3MF geometry capabilities

PrintStash retains every imported Artifact's original bytes. Geometry analysis,
STL viewer conversion and thumbnails are independent derived outputs; refusing
geometry does not prevent downloading the original or using a validated embedded
preview.

## Interpreted geometry

The bounded XML reader supports Core triangle meshes, component assemblies and
build placements. It resolves the primary model through the OPC relationship,
then follows reachable objects and referenced model parts. Unrelated `.model`
parts do not enter measurements. Whole-package archive safety checks still apply
to unreachable entries.

Resource coordinates and affine transforms remain float64. Supported physical
units are micron, millimeter, centimeter, inch, foot and meter; outputs use
millimetres. Nested nonsingular transforms, nonuniform scales and reflections are
supported. Bounds describe vertices referenced by triangles. Legacy slicer
`printable="false"` and `printable="0"` build items are excluded as a compatibility
rule, even though Core 1.4 does not declare that attribute.

The supported Production extension subset is external model-part references
through `p:path`, including referenced component objects and their transforms.
This is **not full Production extension conformance** or a claim of support for
every 3MF extension. Materials, appearance and optional slicer metadata are not
interpreted as additional geometry capabilities. They remain in the original.

## Required capabilities and refusals

Each reached model part's `requiredextensions` prefixes resolve against its XML
namespace declarations. The URI determines the capability; a familiar prefix
bound to an unknown URI is still refused. Core and the supported Production
reference subset are recognized. An unknown required namespace raises a typed
unsupported-capability refusal rather than producing incomplete Core-only
geometry. An undeclared required prefix is invalid source input.

| Output | Unknown required capability |
| --- | --- |
| Mesh metadata | Failed derivative with `failure_reason="unsupported_capability"`; no successful measurement publication |
| Similarity fingerprint, when enabled | Unsupported geometry evidence with `failure_code="unsupported_3mf_capability"`; no usable geometry descriptor |
| On-demand viewer STL | First request accepts preparation; terminal response is HTTP 422 with `detail="unsupported_capability"` |
| Embedded thumbnail | A validated embedded image can still become a ready thumbnail |
| Original download / slicer handoff | Original Artifact bytes remain available through the usual authorization checks |

Refusals for malformed input and exhausted package or geometry budgets remain
separate from unsupported capabilities. Failed outputs retain their terminal
reason for unchanged bytes and recipe; repeated viewer requests do not restart
conversion. Explicit retry or a new recipe can make work eligible again.

Previously calculated similarity results can become stale when interpretation
changes. Re-run analysis before confirming a match or creating a multipart
Model from stale evidence.

## Reader and limits

ZIP entries, decoded bytes, XML documents, reference traversal and expanded faces
remain bounded. Loading and STL conversion run under supervised worker memory and
time limits. A small archive with too many placements can still be refused.

See [compute settings](compute.md#settings) for the XML and memory budgets.

## Sliced archives (Unreleased)

An explicit `.gcode.3mf` filename identifies a G-code Revision, even when the
archive also contains source meshes. The original archive remains downloadable.
The G-code viewer lists its canonical `Metadata/plate_<N>.gcode` members; the
Send dialog chooses which plate to transfer through the existing printer
provider. Queued jobs, batch copies and print history retain that choice.
See [provider support](provider-support.md#sliced-g-code-archives-unreleased)
for transport limits. Plain `.3mf` projects continue to use the geometry
capabilities described above and can preview embedded G-code when present.
