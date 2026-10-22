# ADR-0019: Keep design sources as Artifacts, linked to the files exported from them

Status: Proposed — design for #544.

## Context

PrintStash models what happens after a design is exported: printable
Artifacts, G-code Revisions and print history. It does not model the editable
design itself: the OpenSCAD script, FreeCAD or Fusion model, or Blender scene
that the STL came from. People who print keep those files to fix, resize or
remix a part later. Model sites often ship them next to the STLs.

The obvious homes do not fit:
- **Documents** are unversioned items in a collection and are not owned by a
  Model.
- **Ordinary Artifacts** are assumed to be printable meshes. Ingestion,
  metadata, previews, similarity and Renditions (ADR-0018) all depend on that.

A design source also cannot be turned into a mesh without running its
authoring application, which PrintStash will not do.

## Decision

1. **A Design Source is an Artifact with the `design` role.** Every Artifact has
   exactly one role from a closed set:
   - `printable`: mesh and B-rep formats;
   - `sliced`: G-code;
   - `drawing`: DXF;
   - `design`: new.

   The role is a pure function of `FileType` and is never stored separately,
   so a role and a file type can never disagree. The existing roles describe
   current behaviour; this decision changes only by adding `design`.
2. **The formats are a closed list.** OpenSCAD `.scad`, FreeCAD `.FCStd`, Fusion
   `.f3d` and Blender `.blend`, as new `FileType` members (TEXT + CHECK). A new
   format is a deliberate enum extension with its own extraction rules, never a
   generic "other file".
3. **Never executed, never converted.** PrintStash produces no geometry from a
   design source: no metadata, viewer mesh, similarity fingerprints or
   Renditions (ADR-0018, footnote 11). Only static, bounded extraction runs, as
   derivatives with their own recipes:
   - **thumbnail:** for `.FCStd`, the ZIP entry `thumbnails/Thumbnail.png`,
     which FreeCAD writes when "Save thumbnail into project" is enabled; for
     `.blend`, the embedded preview in its `TEST` block, decompressing the file
     first when it is compressed. A missing preview is `skipped`. `.scad` and
     `.f3d` have none. No design is ever rendered.
   - **search text:** OpenSCAD source is plain text and becomes bounded Search
     Passages of its Model, so variable names and comments are searchable.
     Binary formats contribute only their filename.

   Extraction runs in the same bounded, supervised Jobs as other derivatives and
   never inline in a request.
4. **A design source never defines a Model.** Model identity remains the
   source-mesh SHA-256. A design source attaches to a Model that has a live
   printable Artifact, or arrives in the same upload or ZIP as one. A
   design-only upload is not turned into a Model: ingestion asks the user to
   choose a Model, or refuses with a clear reason. Allowing design-only Models
   (work in progress with no export yet) is a separate decision, because such a
   Model would have no identity, preview or measurements.
5. **Full Artifact lifecycle, except mesh-only behaviour.** Design sources are
   stored through `persist_artifact` and get versions, storage ownership,
   trash, backups and library transfer. Unlike Renditions they cannot be
   regenerated, so backups always include them.
6. **"Exported from" is declared lineage.** A printable Artifact version may
   name at most one design source version it was exported from. One design
   version may have many exports. The link is:
   - **declared by a user,** or **confirmed from a suggestion**. PrintStash
     suggests a link only from strong evidence: the same file stem in the same
     upload or ZIP. It never creates a link silently, because a wrong link would
     point someone at the wrong design.
   - **hidden while either end is trashed,** and restored with it.
   - **deleted when either end is hard-deleted.**
7. **Staleness is shown, never acted on.** When a linked design source has a
   newer live version than the one an export names, the export shows "the
   design changed since this file was exported". PrintStash cannot re-export,
   because that would mean running the design.

## Lifecycle

```
design source v5 ──exported from──▶ printable Artifact (STL v3) ──▶ G-code Revision ──▶ print
      │                                     │
      └── v6 uploaded ──▶ "design changed since this file was exported"
```

Revisions belong to their Model, not to the printable Artifact they were
sliced from. The chain from design to print is therefore complete only at
Model level. A Revision → Artifact link, for example from OrcaSlicer's native
context, is a separate follow-up.

## Integration

| Subsystem | Behaviour |
|---|---|
| Ingestion | Accepted onto a Model with a printable Artifact, or together with one in a single upload or ZIP. Never starts a Model on its own. |
| Model identity, dedup | Never contributes. An identical design file uploaded to the same Model follows ordinary Artifact version rules. |
| Derivatives | Only the static thumbnail and search-text kinds above. No metadata, viewer, fingerprint or Rendition kinds. |
| Renditions | Never a conversion source (ADR-0018). |
| Similarity | Excluded: there is no geometry. |
| Search | OpenSCAD text as bounded Passages; filename for every format. |
| Trash, backups, library transfer | Full Artifact behaviour. Included in backups. |
| Downloads, shares | The original file, through the existing download contract. |
| Open in | Slicers are never offered. The Open in registry (#547) offers the authoring app: Download and open by default, and one click through the Blender extension (#548). |
| UI | A separate "Design" section on the Model with versions, thumbnails where they exist, and lineage. Design sources never appear as printable files. |

## Follow-ups

- **Open in registry (#547).** Replaces the hard-coded slicer list with
  handlers keyed by role and file type. For design apps the default is
  Download and open, because no URL scheme is known for Blender, FreeCAD or
  OpenSCAD that opens a remote file.
- **Blender extension (#548).** Browse, import, and a round-trip in which
  Ctrl+S saves a new design-source version. "Send to PrintStash" uploads exports
  with the "exported from" link set automatically. It builds on **paired apps**,
  which generalize browser pairing into kinds whose allowed actions are fixed in
  code, and on checkouts with a base version. Every uploaded version
  records the version it was `based_on`, so concurrent edits become flagged new
  versions and are never overwritten. FreeCAD and OpenSCAD clients can reuse
  the same server model later.

Generating printable files from design sources stays rejected (see below),
including the narrower case of a sandboxed OpenSCAD export.

## Alternatives considered

- **Documents.** Rejected. Documents are unversioned, live in a collection
  rather than a Model, and have no lineage to Artifacts.
- **A generic attachment for any file.** Rejected. An open set cannot promise
  any behaviour (extraction, search, safety), and it would leave the role of a
  file unknowable.
- **Generating meshes from design sources** (for example by running OpenSCAD).
  Rejected. It executes untrusted code and adds a heavy dependency; for
  FreeCAD, Fusion and Blender it is impractical. ADR-0018 refuses the
  conversion for the same reason.
- **Inferring lineage automatically.** Rejected. Matching names and times are
  evidence for a suggestion, not proof.
- **Design-only Models now.** Deferred (decision 4). It needs its own answer
  for identity, previews and browsing.

## Consequences

- `FileType` gains four members, with an autogenerated migration (SQLite
  through `op.batch_alter_table`).
- A new link table holds "exported from", with at most one design source
  version per printable Artifact version.
- `CONTEXT.md` gains **Design Source** (avoid: project file, attachment, CAD
  file) and **exported from** when this is implemented.
- Thumbnail and search-text derivatives gain design-source recipes. `.blend`
  extraction must handle compressed files within the existing bounded-read
  limits.
- Pending Imports and URL capture must keep a design source with its printable
  files when an import contains both.
