"""Native OrcaSlicer 2.5 plugin. Install the wheel, not this source module.

The export callback is synchronous and runs on Orca's worker. Never retain host
objects, mutate the process environment, open UI, or raise into the export path.
"""

from __future__ import annotations

import json
import sys
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

import orca
import printstash_orca_push as upload


class RevisionStatus(str, Enum):
    NEEDS_TEST = "needs_test"
    KNOWN_GOOD = "known_good"
    FAILED = "failed"
    ARCHIVED = "archived"


DEFAULT_CONFIG = {
    "enabled": False,
    "url": None,
    "username": None,
    "api_key": None,
    "collection": None,
    "strict_mapping": False,
    "revision_label": None,
    "revision_notes": None,
    "revision_status": RevisionStatus.NEEDS_TEST.value,
    "recommended": False,
}


def parse_config(raw: str) -> dict:
    """Validate Orca's JSON boundary, including configurations edited as JSON."""
    stored = json.loads(raw)
    if not isinstance(stored, dict) or stored.keys() - DEFAULT_CONFIG.keys():
        raise ValueError("Invalid PrintStash configuration fields")
    config = DEFAULT_CONFIG | stored
    for key, default in DEFAULT_CONFIG.items():
        value = config[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise TypeError(f"{key} must be a boolean")
        elif value is not None:
            if not isinstance(value, str):
                raise TypeError(f"{key} must be text")
            config[key] = value.strip() or None
    config["revision_status"] = RevisionStatus(config["revision_status"]).value
    if config["enabled"]:
        if any(config[key] is None for key in ("url", "username", "api_key")):
            raise ValueError("Set the PrintStash URL, username and API key")
        url = urlsplit(config["url"])
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("Use an HTTP(S) server URL without credentials or query")
        # Accessing port validates malformed and out-of-range values.
        _ = url.port
        config["url"] = config["url"].rstrip("/")
    return config


def config_ui() -> str:
    """Use Orca's host-owned configuration bridge and native theme defaults."""
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PrintStash settings</title>
<style>
body { margin: 20px; font-family: var(--orca-font, sans-serif); }
form { display: grid; gap: 14px; max-width: 640px; }
label { display: grid; gap: 5px; }
.check { display: flex; align-items: center; gap: 8px; }
input:not([type=checkbox]), select, textarea { box-sizing: border-box; width: 100%; }
small { color: var(--orca-muted); }
button { justify-self: start; }
</style>
<h1>PrintStash</h1><p>Save exported G-code in your print library.</p>
<form id="settings">
<label class="check"><input id="enabled" type="checkbox">Upload exports to PrintStash</label>
<label>Server URL<input id="url" type="url" placeholder="https://printstash.example.com"></label>
<label>Username<input id="username" autocomplete="off"></label>
<label>API key<input id="api_key" type="password" autocomplete="off"></label>
<small>Create a key in PrintStash Settings. Orca stores it in its plugin configuration;
keep configurations containing credentials private.</small>
<label>Collection (optional)<input id="collection" placeholder="Functional/Brackets"></label>
<label class="check"><input id="strict_mapping" type="checkbox">Only upload a single matched source model</label>
<small>Otherwise, plates with multiple objects or no source identity become standalone models.</small>
<label>Revision label (optional)<input id="revision_label"></label>
<label>Revision notes (optional)<textarea id="revision_notes" rows="3"></textarea></label>
<label>Revision status<select id="revision_status">
<option value="needs_test">Needs test</option><option value="known_good">Known good</option>
<option value="failed">Failed</option><option value="archived">Archived</option></select></label>
<label class="check"><input id="recommended" type="checkbox">Make this revision recommended</label>
<button type="submit">Save settings</button><p id="status" role="status"></p>
</form><script>
const defaults = """
        + json.dumps(DEFAULT_CONFIG)
        + """;
const current = {...defaults, ...window.orca.getConfig()};
for (const key of Object.keys(defaults)) {
  const input = document.getElementById(key);
  if (input.type === 'checkbox') input.checked = current[key];
  else input.value = current[key] ?? '';
}
document.getElementById('settings').onsubmit = (event) => {
  event.preventDefault();
  const value = {};
  for (const key of Object.keys(defaults)) {
    const input = document.getElementById(key);
    value[key] = input.type === 'checkbox' ? input.checked : (input.value.trim() || null);
  }
  const status = document.getElementById('status');
  if (value.enabled && (!value.url || !value.username || !value.api_key)) {
    status.textContent = 'Enter the server URL, username and API key.';
    return;
  }
  window.orca.saveConfig(value);
  status.textContent = 'Settings sent to OrcaSlicer for saving.';
};
</script></html>"""
    )


class PrintStashUpload(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Upload to PrintStash"

    def get_default_config(self):
        return dict(DEFAULT_CONFIG)

    def has_config_ui(self):
        return True

    def get_config_ui(self):
        return config_ui()

    def execute(self, ctx):
        if ctx.step != orca.slicing.Step.psGCodePostProcess:
            return orca.ExecutionResult.skipped()
        try:
            config = parse_config(self.get_config())
            if not config["enabled"]:
                return orca.ExecutionResult.skipped("PrintStash uploads are disabled")
            gcode = Path(ctx.gcode_path)
            # This is the export's resolved config, never the live GUI model or
            # global SLIC3R_* environment (which can describe another plate).
            environ = {}
            for key in (
                "printer_model",
                "printer_settings_id",
                "print_settings_id",
                "filament_type",
                "filament_settings_id",
            ):
                value = ctx.config_value(key)
                if value is not None:
                    environ["SLIC3R_" + key.upper()] = value
            context = upload.extract_native_context(gcode, environ)
            fields = {
                "native_context": json.dumps(
                    context, sort_keys=True, separators=(",", ":")
                ),
                "submission_id": upload.submission_id(gcode, context),
                "strict_mapping": str(config["strict_mapping"]).lower(),
                "collection": config["collection"],
                "revision_label": config["revision_label"],
                "revision_notes": config["revision_notes"],
                "revision_status": config["revision_status"],
                "is_recommended": str(config["recommended"]).lower(),
            }
            filename = upload.output_name(
                gcode, {"SLIC3R_PP_OUTPUT_NAME": ctx.output_name}
            )
            token = upload.login(
                config["url"], config["username"], config["api_key"], None
            )
            if token is None:
                return self._skipped("Login failed or network permission is pending")
            # Do not keep retrying a network outage inside Orca's export worker.
            # Re-exporting retries with the same deterministic submission ID.
            if not upload.push(
                config["url"], token, gcode, fields, retries=1, filename=filename
            ):
                return self._skipped("Upload failed or network permission is pending")
            return orca.ExecutionResult.success("PrintStash accepted the upload")
        except Exception as error:  # noqa: BLE001 — export must always proceed
            # Exception messages/URLs can contain credentials; log only the type.
            return self._skipped(
                f"Upload skipped ({type(error).__name__}); check PrintStash settings and Orca permissions"
            )

    @staticmethod
    def _skipped(message):
        safe_message = "PrintStash: " + message + ". G-code export continues."
        print(safe_message, file=sys.stderr)
        # RecoverableError also aborts export in Orca 2.5's PostProcessor.cpp.
        return orca.ExecutionResult.skipped(safe_message)


@orca.plugin
class PrintStashPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(PrintStashUpload)
