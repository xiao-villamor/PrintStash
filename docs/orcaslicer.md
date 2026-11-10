# OrcaSlicer integration

PrintStash can upload exported G-code directly from an OrcaSlicer 2.5 plugin.
The plugin runs in Orca's embedded Python; no external Python installation or
post-processing command is needed. Earlier Orca versions can use the legacy
[post-processing script](#legacy-post-processing-script).

Both integrations attach a single identified source object to its existing Model
as a new G-code Revision. The source STL, 3MF, OBJ, STEP/STP, or DXF must already
exist in PrintStash. Neither integration guesses lineage from the output G-code
name, a fuzzy match, or a user-maintained mapping file.

## Install the native plugin (OrcaSlicer 2.5)

1. Download `printstash_orca_plugin-0.1.0-py3-none-any.whl` from the
   `printstash-orca-plugin` artifact of a successful PrintStash CI run, or build
   it using the command below. Extract the CI artifact ZIP to get the wheel.
2. Open Orca's **Plugins** dialog. Use **Browse plugins → Install local plugin**
   and select the wheel. Activate the plugin and its **Upload to PrintStash**
   capability.
3. Open the capability's **Config** tab. Enter the PrintStash server URL,
   username and API key, set any collection or revision options, enable
   **Upload exports to PrintStash**, and save.
4. In the Process preset's advanced options, select **Upload to PrintStash** in
   **Slicing Pipeline Plugin** (`slicing_pipeline_plugin`). Save the preset.
5. Remove the old `printstash_orca_push.py` command from that preset's
   **Post-processing scripts** field, then slice and export or send the G-code.
   Use only one PrintStash integration per preset.

The plugin only acts at `psGCodePostProcess`, after normal post-processing
scripts. It reads the completed working G-code and the output filename provided
by Orca. It does not change G-code or depend on global `SLIC3R_*` variables.
Native G-code object labels and the optional markers below supply source identity;
keep **Label objects** enabled for source matching without markers. Resolved
printer, process and filament settings come from this export's plugin context.

Orca asks before allowing network access. Approve access to your PrintStash
server when prompted. On a slicing worker, Orca denies the current operation
while showing a permission prompt; **export again after approving**. HTTP URLs
and socket addresses are separate grants, so first setup may require more than
one attempt. Reinstalling a plugin resets its grants. The plugin does not bypass
this permission system or open dialogs from the slicing worker.

Uploads start disabled. Configuration is stored by Orca, including the API key;
it is not an encrypted credential vault. Keep credentials in the global plugin
configuration and avoid sharing configurations or preset overrides containing
keys. Create a dedicated revocable key in PrintStash Settings.

PrintStash failures return a skipped plugin result and let export continue.
Diagnostics go to Orca's log. Each callback makes one login and one upload
attempt, each with a 30-second socket timeout. Export waits for these requests;
there is no durable offline queue. Exporting again retries using the existing
deterministic submission ID. A success means PrintStash accepted the upload;
check PrintStash for the ingestion job's final result.

### Build the wheel

From the PrintStash checkout:

```sh
uv build --wheel --out-dir dist/orca-plugin integrations/orcaslicer
python3 integrations/orcaslicer/test_wheel.py dist/orca-plugin/*.whl
```

The wheel includes both the native plugin and the shared upload/parser module.
It has no third-party runtime dependencies and supports Python 3.12 or newer.
Its host API targets the released OrcaSlicer 2.5.0 bindings. Automated tests
exercise an Orca host fake against a real PrintStash HTTP server; they do not
replace a smoke test of installation, permissions and export in the Orca GUI.

Upstream references: [plugin development](https://www.orcaslicer.com/wiki/developer_reference/plugin_development/plugin_development),
[slicing capability](https://github.com/OrcaSlicer/OrcaSlicer/blob/v2.5.0/src/slic3r/plugin/pluginTypes/slicingPipeline/SlicingPipelinePluginCapability.cpp),
and [export result handling](https://github.com/OrcaSlicer/OrcaSlicer/blob/v2.5.0/src/slic3r/GUI/PostProcessor.cpp).

## Legacy post-processing script

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

## Inspect captured settings in Profiles

After a G-code upload finishes processing, open **Profiles → Filaments** or
**Printers** and open **Details** on the corresponding profile. Profiles start
collapsed with a compact summary. Details show notes and captured slicer
configurations in a reading view. Choose **Edit** to reveal the profile fields
and multiline notes editor; closing the editor saves valid changes and keeps
unfinished drafts available. The slicer-settings section and each variant can
also be collapsed. Material and brand match filament observations; the machine model
matches printer observations. Matching is case-insensitive and ignores surrounding
spaces. A name alone is never enough; profiles without those attributes have no
associated observations. Changing those attributes changes the observations shown.

Equal settings with the same slicer version and machine context form one variant.
Different preset names or setting values stay separate. Missing slicer or machine
identity keeps observations separate. Expand **View settings** to search values
and inspect source files, upload dates and Model links. Process settings (layer
height, infill, speeds) remain attached to each source file. Multiple filament
values preserve their original serialization and order as a complete observed
configuration, rather than being assigned individually to spools.

Sources load in bounded pages on demand. **Load more source files** merges matching
configurations into the variants already shown. Counts describe the loaded files;
the page explicitly indicates when more remain. No saved profile fields, costs,
notes or Spoolman values are replaced by these observations.

The capture reads supported setting comments from the beginning and end of the
G-code, including supported binary G-code metadata. Availability depends on what
the slicer exported: a missing setting does not imply its default value. Existing
G-code files are reprocessed in the background after upgrading; use **Refresh**
as processing completes. Only live files in Models you can access are listed.

This first version captures standalone G-code files. Profile details do not yet inspect embedded plate G-code or project presets inside `.3mf` or
`.gcode.3mf` containers.

These entries describe individual prints. They are not a full slicer preset
backup and cannot restore a complete OrcaSlicer or Bambu Studio configuration.
Unused presets, inherited defaults and project overrides may be absent. Custom
G-code, post-processing scripts and connection credentials are not captured.
Observed values are read-only details inside the editable profile.

The read-only `GET /api/v1/slicer-profiles/{kind}/{profile_id}` endpoint
(`kind` is `filament` or `printer`) filters observations for a catalog profile.
The original `GET /api/v1/slicer-profiles` endpoint remains available. Both return `items` and
`next_before_id`. Follow that cursor even if a page has no items: older files
without captured settings may still be waiting for reprocessing. `limit`
defaults to 20 and is capped at 50.

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
