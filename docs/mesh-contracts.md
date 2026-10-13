# Mesh result contracts

Mesh admission, loading, measurements and embedded preview extraction each have a
single owner. See [the owner and temporary facade inventory](architecture/mesh-owners.md).
The shared contracts import data definitions without loading an execution owner.

## Independent coverage facts

`ThumbnailResult.coverage` records three different facts:

- `source_scan`: unread, complete or partial source reading.
- `geometry`: no prepared geometry, complete geometry or an explicitly sampled representation.
- `preview`: no image, a document-supplied image, complete representation or partial representation.

A complete source scan may coexist with sampled geometry or a partial preview.
An embedded image does not certify that geometry was read. The fallback scan flag
certifies only source reading; preview coverage also checks the retained triangle
count. Renderer-internal buffers do not certify prepared analysis geometry.

`PreparedMesh` retains a typed `Trimesh` and resource/placement scene. Complete and
sampled geometry are distinct cases; a sample cannot claim complete resources,
B-rep evidence or a whole-resource alias. Its derived `complete` property refers
only to prepared geometry. Successful fingerprint results require meaningful
records and coherent whole/component identities; partial results require a sampled
recipe and sampling cause. Error outcomes cannot carry successful records. New
worker causes are closed values; historical stored diagnostic text is unchanged.

The native codec requires all coverage fields and rejects unknown causes or
contradictory result facts. READY analysis requires complete geometry; PARTIAL
analysis requires sampled geometry with the same cause. Refused measurement and
a useful preview remain independent.

The persisted thumbnail `output_json.complete` field continues to describe the
preview. Mesh thumbnail recipe8 re-derives recipe7 outputs, whose old flag could
conflate a completed scan with all source triangles being represented. Existing
outputs remain visible until a replacement succeeds. Geometry values, numerical
fingerprints and public HTTP schemas are unchanged.
