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
    """Render the settings form through Orca's configuration and theme bridge."""
    return r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PrintStash settings</title>
<style>
:root {
  --background: var(--orca-bg, #ffffff);
  --foreground: var(--orca-fg, #202938);
  --muted: var(--orca-muted, #596578);
  --border: var(--orca-border, #dce1e8);
  --primary: #2563eb;
  --primary-foreground: #ffffff;
  --surface: color-mix(in srgb, var(--foreground) 4%, var(--background));
  --ring: var(--orca-accent, #2563eb);
  --radius: 6px;
  color-scheme: light;
}
html[data-orca-theme="dark"] { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--background); color: var(--foreground); font: 14px/1.5 var(--orca-font, "Segoe UI", sans-serif); }
main { max-width: 820px; margin: 0 auto; padding: 20px; }
h1, h2, p { margin: 0; }
h1 { font-size: 23px; font-weight: 650; line-height: 1.2; }
h2 { font-size: 15px; font-weight: 600; }
header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.mark { width: 36px; height: 36px; color: var(--primary); flex-shrink: 0; }
.subtitle, .hint, .scope, .section-description { color: var(--muted); }
.subtitle { margin-top: 5px; }
.scope { padding: 8px 12px; border-left: 3px solid var(--border); background: var(--surface); margin-bottom: 16px; font-size: 12px; }
.scope strong { color: var(--foreground); font-weight: 600; }
fieldset { border: 0; margin: 0; padding: 0; min-width: 0; }
.enable { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-bottom: 14px; border-bottom: 1px solid var(--border); }
.enable label { font-weight: 600; }
.switch { appearance: none; width: 38px; height: 22px; flex-shrink: 0; margin: 0; border: 1px solid var(--border); border-radius: 20px; background: var(--muted); position: relative; }
.switch::after { content: ""; position: absolute; width: 16px; height: 16px; left: 2px; top: 2px; background: var(--primary-foreground); border-radius: 50%; }
.switch:checked { background: var(--primary); border-color: var(--primary); }
.switch:checked::after { transform: translateX(16px); }
section { padding: 16px 0; border-bottom: 1px solid var(--border); }
.section-description { margin: 3px 0 12px; font-size: 12px; }
.fields { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); gap: 12px 16px; }
.full { grid-column: 1 / -1; }
.field { display: grid; gap: 6px; min-width: 0; }
.field > label { font-size: 12px; font-weight: 600; }
.optional { color: var(--muted); font-weight: 400; }
input:not([type=checkbox]), select, textarea { width: 100%; min-width: 0; min-height: 36px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--background); color: var(--foreground); font: inherit; padding: 7px 10px; }
textarea { resize: vertical; min-height: 72px; }
input::placeholder, textarea::placeholder { color: var(--muted); opacity: 0.8; }
:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
.key-field { display: flex; position: relative; }
.key-field input { padding-right: 60px; }
.key-field button { position: absolute; right: 4px; top: 4px; min-height: 28px; border: 0; padding: 3px 8px; font-size: 12px; background: transparent; }
.hint { font-size: 12px; line-height: 1.5; }
.check { display: flex; align-items: flex-start; gap: 8px; }
.check input { margin: 4px 0 0; accent-color: var(--primary); flex-shrink: 0; }
.check label { font-size: 13px; }
.check .hint { display: block; margin-top: 2px; }
.profile-note { background: var(--surface); padding: 10px; margin-top: 12px; border-radius: var(--radius); }
.profile-note strong { display: block; font-size: 12px; font-weight: 600; margin-bottom: 3px; }
details { padding-top: 14px; }
summary { font-weight: 600; font-size: 13px; }
details .fields { margin-top: 16px; }
footer { position: sticky; bottom: 0; background: var(--background); padding: 12px 0 0; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
button { font: inherit; font-weight: 600; min-height: 36px; padding: 7px 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--background); color: var(--foreground); }
button:active:not(:disabled) { transform: scale(0.98); }
.primary { background: var(--primary); color: var(--primary-foreground); border-color: var(--primary); }
button:disabled, input:disabled, select:disabled, textarea:disabled { opacity: 0.6; }
#status { font-size: 12px; color: var(--muted); flex: 1; min-width: 160px; }
[hidden] { display: none !important; }
@media (max-width: 540px) { main { padding: 16px; } .fields { grid-template-columns: minmax(0, 1fr); } h1 { font-size: 21px; } footer { align-items: stretch; } #status { flex-basis: 100%; } }
</style>
</head>
<body><main>
<header>
<svg class="mark" viewBox="0 0 36 36" fill="none" aria-hidden="true"><path d="m18 3 13 7.5v15L18 33 5 25.5v-15L18 3Z" stroke="currentColor" stroke-width="2"/><path d="m5 10.5 13 7.5 13-7.5M18 18v15M11.5 6.75l13 7.5v7.5" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>
<div><h1>PrintStash</h1><p class="subtitle">Save each export in your print library.</p></div>
</header>
<div class="scope" id="scope"></div>
<form id="settings" novalidate>
<fieldset id="controls">
<div class="enable"><div><label for="enabled">Upload exports to PrintStash</label><p class="hint">Runs when you export G-code from this process.</p></div><input class="switch" id="enabled" type="checkbox"></div>
<section aria-labelledby="connection-heading">
<h2 id="connection-heading">Connection</h2>
<p class="section-description">Use the same server and account as your PrintStash app.</p>
<div class="fields">
<div class="field full"><label for="url">Server URL</label><input id="url" type="url" placeholder="http://localhost:3000" spellcheck="false" autocomplete="off"></div>
<div class="field"><label for="username">Username</label><input id="username" autocomplete="off" spellcheck="false"></div>
<div class="field"><label for="api_key">API key</label><div class="key-field"><input id="api_key" type="password" autocomplete="off" spellcheck="false" aria-describedby="key-help"><button id="show-key" type="button" aria-label="Show API key" aria-pressed="false">Show</button></div></div>
<p class="hint full" id="key-help">Create a key in PrintStash Settings → API keys. Keep connection credentials in global plugin settings when sharing presets.</p>
</div>
</section>
<section aria-labelledby="export-heading">
<h2 id="export-heading">Export options</h2>
<p class="section-description">Choose where exports go and how they match your models.</p>
<div class="fields">
<div class="field full"><label for="collection">Collection <span class="optional">(optional)</span></label><input id="collection" placeholder="Functional/Brackets"></div>
<div class="check full"><input id="strict_mapping" type="checkbox"><div><label for="strict_mapping">Require a single matching source model</label><span class="hint">When off, multi-object plates or exports without source identity can become standalone models.</span></div></div>
</div>
<aside class="profile-note"><strong>Print settings travel with your G-code</strong><p class="hint">PrintStash captures available printer, filament and process settings in Profiles. These are snapshots from each export; unused Orca presets are not synchronized.</p></aside>
</section>
<details id="revision-options"><summary>Revision defaults</summary>
<div class="fields">
<div class="field"><label for="revision_label">Revision label <span class="optional">(optional)</span></label><input id="revision_label" placeholder="e.g. Stronger walls"></div>
<div class="field"><label for="revision_status">Revision status</label><select id="revision_status"><option value="needs_test">Needs test</option><option value="known_good">Known good</option><option value="failed">Failed</option><option value="archived">Archived</option></select></div>
<div class="field full"><label for="revision_notes">Revision notes <span class="optional">(optional)</span></label><textarea id="revision_notes" rows="2" placeholder="What changed in this print?"></textarea></div>
<div class="check full"><input id="recommended" type="checkbox"><label for="recommended">Make this revision recommended</label></div>
</div></details>
<footer><button class="primary" type="submit">Save settings</button><button id="restore-global" type="button" hidden>Use global settings</button><p id="status" role="status" aria-live="polite"></p></footer>
</fieldset>
</form>
<script>
const defaults = __DEFAULT_CONFIG__;
const host = window.orca;
const form = document.getElementById('settings');
const status = document.getElementById('status');
let baseline;
let pending = false;
let restoring = false;

function readForm() {
  const value = {};
  for (const key of Object.keys(defaults)) {
    const input = document.getElementById(key);
    value[key] = input.type === 'checkbox' ? input.checked : (input.value.trim() || null);
  }
  return value;
}
function showConfig(saved) {
  const current = {...defaults, ...saved};
  for (const key of Object.keys(defaults)) {
    const input = document.getElementById(key);
    if (input.type === 'checkbox') input.checked = current[key];
    else input.value = current[key] ?? '';
  }
  baseline = JSON.stringify(readForm());
  const context = host.getContext();
  const preset = context.scope === 'preset';
  const scope = document.getElementById('scope');
  scope.replaceChildren();
  const title = document.createElement('strong');
  title.textContent = preset ? 'This process preset. ' : 'Global settings. ';
  scope.append(title, preset
    ? 'Changes here override global settings. Configure your shared connection in Plugins → Upload to PrintStash → Config.'
    : 'Used by every preset without its own override.');
  document.getElementById('controls').disabled = context.readOnly === true;
  document.getElementById('restore-global').hidden = !preset || !context.hasPresetOverride;
  document.getElementById('revision-options').open = Boolean(current.revision_label || current.revision_notes || current.recommended || current.revision_status !== defaults.revision_status);
  status.textContent = context.readOnly ? 'These settings are read-only.'
    : restoring ? 'Using global settings.' : pending ? 'Settings saved.'
    : current.enabled ? 'Uploads enabled.' : 'Uploads paused.';
  pending = false;
  restoring = false;
}
host.onConfig(showConfig);
form.addEventListener('input', () => {
  status.textContent = JSON.stringify(readForm()) === baseline ? 'No unsaved changes.' : 'Unsaved changes.';
});
document.getElementById('show-key').onclick = (event) => {
  const input = document.getElementById('api_key');
  const visible = input.type === 'password';
  input.type = visible ? 'text' : 'password';
  event.currentTarget.textContent = visible ? 'Hide' : 'Show';
  event.currentTarget.setAttribute('aria-label', visible ? 'Hide API key' : 'Show API key');
  event.currentTarget.setAttribute('aria-pressed', String(visible));
};
document.getElementById('restore-global').onclick = () => {
  restoring = true;
  status.textContent = 'Asking Orca to restore global settings…';
  host.restoreDefaults();
};
form.onsubmit = (event) => {
  event.preventDefault();
  if (host.getContext().readOnly) return;
  const value = readForm();
  if (value.enabled) {
    const missing = ['url', 'username', 'api_key'].find(key => !value[key]);
    if (missing) {
      status.textContent = 'Enter the server URL, username and API key.';
      document.getElementById(missing).focus();
      return;
    }
    try {
      const url = new URL(value.url);
      if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash) throw new Error('invalid-url');
    } catch {
      status.textContent = 'Use an HTTP(S) server URL without credentials, query or fragment.';
      document.getElementById('url').focus();
      return;
    }
  }
  pending = true;
  status.textContent = 'Sent to OrcaSlicer for saving…';
  host.saveConfig(value);
};
</script>
</main></body></html>
""".replace("__DEFAULT_CONFIG__", json.dumps(DEFAULT_CONFIG))


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
