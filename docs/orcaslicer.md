# OrcaSlicer native upload hook

PrintStash's dependency-free OrcaSlicer hook uploads an exported G-code file and,
when Orca identifies exactly one source object, attaches it to the existing Model
as a new G-code Revision. The source STL, 3MF, OBJ, STEP/STP, or DXF must already
exist in PrintStash. The hook never creates source lineage from the output G-code
name, a fuzzy match, or a user-maintained mapping file.

## Configure OrcaSlicer

Use Python 3 to run [`scripts/printstash_orca_push.py`](../scripts/printstash_orca_push.py)
as a post-processing script under **Process → Others**. Orca appends the exported
file path as the final argument:

```text
/usr/bin/python3 /path/to/printstash_orca_push.py --url https://printstash.example.com --username YOUR_USERNAME --api-key psk_YOUR_KEY --collection "Functional/Brackets"
```

The script uses only Python's standard library. Keep the API key out of the
script file and out of Orca custom G-code. `PRINTSTASH_URL`,
`PRINTSTASH_USERNAME`, and `PRINTSTASH_API_KEY` can supply the three connection
arguments instead.

Orca runs post-processing scripts on a temporary `<name>.pp` copy of the export
and passes the real name in `SLIC3R_PP_OUTPUT_NAME`; the hook uploads under that
real name.

Orca object labels are enough for ordinary single-object exports. To make the
native placeholder evidence explicit across profiles, add these comments to a
custom G-code section that Orca includes in the exported file:

```gcode
; printstash:input_filename={input_filename}
; printstash:first_object_name={first_object_name}
; printstash:num_objects={num_objects}
; printstash:num_instances={num_instances}
; printstash:plate_name={plate_name}
```

Unexpanded placeholders are ignored safely. Where Orca supplies equivalent
`SLIC3R_` runtime values, they take precedence; custom markers then take
precedence over native G-code object labels. `.gcode.3mf` exports are accepted
and contribute their embedded G-code labels and project metadata.

## Mapping behavior

- A single-object export with one exact, live, editable source filename attaches
  to that existing Model. The Revision defaults to `needs_test` and is not made
  recommended automatically.
- A filename present on more than one editable Model returns
  `orca_source_ambiguous`. A named source that does not exist returns
  `orca_source_not_found`. Neither case creates a Model, Revision, or staged job.
- A multi-object plate is never attached to its first object. In the default
  permissive mode it becomes a standalone plate-context Model while retaining
  all object labels. `--strict-mapping` rejects it instead.
- If Orca supplies no usable source identity, permissive mode keeps the legacy
  standalone-upload behavior. Strict mode rejects it before staging bytes.
- The hook hashes the file and normalized context into a deterministic submission
  ID, so its retries resolve to one Revision.

`--revision-label`, `--revision-notes`, `--revision-status`, and `--recommended`
use the existing Revision fields explicitly. `--model-name` and `--tags` are
deprecated compatibility options for context-free standalone uploads; neither
participates in source resolution, and new configurations should omit them.

The hook always exits `0`: unavailable PrintStash, invalid metadata, login
failure, and upload failure must not interrupt Orca's export. Diagnostics go to
`~/.printstash_orca_push.log` by default. To inspect only the normalized,
non-secret context without logging in or uploading, run:

```text
python3 scripts/printstash_orca_push.py --diagnostic-output /tmp/orca-context.json /path/to/export.gcode
```

The diagnostic never dumps the process environment, password, API key, or access
token.

## API contract

`POST /api/v1/ingest/orca` is multipart form data with a required `file` and an
optional version-1 `native_context` JSON string. The hook also sends a 64-character
hex `submission_id`. The normalized context contains `classification`, `source`,
`slicer`, `printer`, `filaments`, `process`, `print_stats`, and `field_sources`.
For example:

```json
{
  "version": 1,
  "classification": "single_object",
  "source": {
    "filename": "bracket.stl",
    "basename": "bracket",
    "first_object_name": "bracket.stl",
    "object_count": 1,
    "instance_count": 2,
    "plate_name": null,
    "project_name": null,
    "object_labels": [
      {"name": "bracket.stl", "object_id": "0", "copy_index": 0},
      {"name": "bracket.stl", "object_id": "0", "copy_index": 1}
    ]
  },
  "slicer": {"name": "OrcaSlicer", "version": "2.3.2"},
  "printer": {"model": "Ender-3 V3 SE", "preset": "Workshop"},
  "filaments": [{"type": "PLA", "preset": "Generic PLA"}],
  "process": {"preset": "0.20mm Standard"},
  "print_stats": {"estimated_time_s": 4296},
  "field_sources": {"source.filename": "gcode_object_label"}
}
```

Destination fields are `collection`, `strict_mapping`, and
`target_library_id`. Revision fields are `revision_label`, `revision_status`,
`revision_notes`, and `is_recommended`. `source_hash`, `model_name`, and `tags`
remain compatibility fields; a client-supplied `source_hash` must agree with the
Model resolved from native context.

Successful intake returns `202` with a Job ID. Poll `GET /api/v1/jobs/{job_id}`;
its terminal result identifies the Model and Artifact. Validation failures use
`422`, an unknown exact source uses `404`, and an ambiguous exact source uses
`409`. The full normalized context is returned as
`files[].metadata.native_context` in Model detail responses.
