# BGCODE toolpath preview

The GCode view can inspect PrusaSlicer BGCODE v1 files directly, including layers
and travel moves. Layer selection follows deposited moves; travel lifts do not
create empty layers. PrintStash validates and converts a temporary copy with official
[libbgcode](https://github.com/prusa3d/libbgcode/tree/d4da9073616d70a43c151e8c1d7fbff879d2e08a).
The stored Artifact, metadata, thumbnail, original download and bytes uploaded to
PrusaLink remain unchanged. PrusaLink remains beta.

Full and lite API images include the converter for amd64 and arm64. Development
installs need the executable on PATH, or `VAULT_BGCODE_EXECUTABLE` pointing to it.
The build script verifies the source archive digest and packages license notices.

## Limits and errors

| Environment setting | Default | Meaning |
|---|---|---|
| `VAULT_TOOLPATH_INPUT_MAX_MB` | 128 | Maximum source MiB |
| `VAULT_TOOLPATH_OUTPUT_MAX_MB` | 32 | Maximum converted MiB |
| `VAULT_TOOLPATH_TIMEOUT_SECONDS` | 30 | Converter wall/CPU time |
| `VAULT_TOOLPATH_MEMORY_MAX_MB` | 512 | Converter address-space MiB |
| `VAULT_TOOLPATH_MAX_JOBS` | 2 | Concurrent previews per API process |

The browser parses in a cancelable Web Worker with a maximum of one million
segments, including tessellated arcs. G92, absolute/relative coordinates and
extrusion, unit changes, and G2/G3 IJK or radius arcs are supported. This is a
geometry viewer, not firmware simulation or a guarantee that a file is printable.

A busy converter offers retry. Oversized, timed-out or invalid files show an error
without displaying a partial conversion. Temporary copies are removed after
success, failure or cancellation. The browser cannot inspect a file beyond these
limits; its original remains downloadable where permitted.

`GET /api/v1/files/{id}/toolpath` reads through Artifact content, covering managed
Local/S3 storage and Library sources with the same access checks as the Artifact.
`GET /api/v1/share/{token}/files/{id}/toolpath` additionally requires the share to
allow original downloads. Responses are private and not cached.

## Verification

The pinned upstream PrusaSlicer fixture is compared byte-for-byte with its ASCII
reference. Tests exercise every supported compression/encoding combination,
checksums and truncation, resource bounds, cancellation cleanup, shared access,
and a real PrusaLink upload after preview. The Docker matrix converts that fixture
inside each full/lite amd64/arm64 image. Real-browser coverage opens the uploaded
BGCODE, changes layers and travel visibility, then checks its original download.
