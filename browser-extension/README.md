# PrintStash Model Importer

This Manifest V3 Chrome and Firefox extension recognizes MakerWorld, Printables, and
Thingiverse model pages, Printables collections, and direct model/archive file
URLs, then sends them to the Pending Imports inbox of a self-hosted PrintStash
instance.

## Install

1. In PrintStash, create a browser pairing code in **Settings → Imports**.
2. Build it with `pnpm install && pnpm build`. In Chrome, open
   `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**,
   and select `browser-extension/.output/chrome-mv3`. For Firefox, run
   `pnpm zip:firefox` and load the local XPI temporarily from
   `about:debugging`.
3. Open a supported source: an individual MakerWorld model (while signed in),
   a Printables model or collection, a Thingiverse model, or a direct model or
   archive file URL. Click the extension, enter the PrintStash URL and pairing
   code, then choose **Pair browser**.
4. Confirm that the popup shows **Connected** with the expected vault hostname
   and username, then choose **Send to Pending Imports**.
5. Review the detected files in **Pending Imports** before completing the
   import.

### Verify a refreshed Chrome popup

After rebuilding, confirm Chrome is loading the exact unpacked directory
`browser-extension/.output/chrome-mv3`, then click **Reload** for PrintStash on
`chrome://extensions`. Close and reopen the popup and confirm its header shows
**Capture protocol v2 · diagnostics 4**. If the marker is missing, Chrome has a stale or
different unpacked directory loaded: use **Load unpacked** to select
`browser-extension/.output/chrome-mv3` again, click **Reload**, and reopen the
popup before testing capture behavior.

For faster setup, open **Settings → Imports** in PrintStash and choose **Create
pairing code** in the Paired browsers card. Then enter that code in the
extension. The one-time code expires after five minutes and locks after five
failed exchange attempts. Chrome still asks for explicit approval before
granting a new vault host permission.

Local vaults may be entered as `localhost:8000`, `127.0.0.1:3000`, or a private
LAN address without a scheme; the extension selects HTTP automatically. Its
manifest grants network access only to loopback addresses up front, while LAN
and remote Vaults continue to request a per-host permission when connected.

The extension verifies the public PrintStash health endpoint before pairing.
The one-time code is exchanged for an opaque browser-only credential; the code
is never retained, and PrintStash stores only its hash. Local extension storage
contains only the vault URL and that device credential—not a username, API key,
or access token. Revoking the browser in PrintStash stops future imports.
**Manage → Disconnect** removes the device credential and vault host permission
from the browser. Existing username/API-key setups continue to work as a legacy
migration path, but new setups should use pairing.

For MakerWorld, the extension asks the already-authenticated page for the
selected model's signed package URL, downloads that package in the browser,
and uploads the bytes to PrintStash. That upload can also include a bounded,
allowlisted source record (canonical URL, provider identity, and safe visible
metadata such as title, description, creator, and license). It never includes
raw HTML or JSON-LD, cookies, session headers, or signed download URLs.
MakerWorld cookies and credentials never leave the browser and are not stored
by PrintStash. MakerWorld collections are not supported; capture their model
pages individually. On Printables, the extension can use the signed-in page to
let the user select files and transfer those bytes directly; server-side
resolution remains available for the limited public fields and choices its
supported endpoints expose. Direct URL capture uses PrintStash's SSRF
protections. Thingiverse remains a user-assisted flow: the extension creates a
bounded metadata draft for user-selected files and never automatically
downloads a package or ZIP.
Direct URLs may point to `.zip`,
`.3mf`, `.stl`, `.obj`, `.step`, `.stp`, `.gcode`, `.g`, `.gco`, or `.bgcode`
files.

The helper does not persist access tokens. The popup links directly to the
Imports settings on the configured vault to create a pairing code.

Capture transfers are user-initiated and go only to the Vault URL you configure.

## Storage and Library sources in 0.13

The extension does not connect to local disks, NAS shares, S3, WebDAV or SFTP.
It never receives their credentials. Every capture first enters **Pending
Imports** through the paired PrintStash API, where a user reviews the selected
files and metadata.

Finalizing a capture writes to managed Vault storage or to a mounted Library
source that explicitly permits create-only write-back. Read-only S3, WebDAV and
SFTP Library sources are discovery destinations, not capture targets, and do
not appear in the upload destination selector. Capturing a page cannot overwrite
or delete a source object.

Upgrading from 0.12 to 0.13 does not require re-pairing a browser. Existing
browser device credentials and legacy username/API-key setups retain their
normal migration behavior. If capture fails after an upgrade:

1. Confirm the popup still shows the expected Vault hostname and user.
2. Open the Vault health endpoint and resolve any read-only storage recovery
   state before retrying finalization.
3. Check **Pending Imports**. A successful browser transfer can remain there
   even when finalization to managed storage was blocked.
4. Rebuild and reload the extension only when the popup protocol marker is
   stale; storage-provider changes alone do not require an extension rebuild.

Run the WXT/Vitest adapter, storage, messaging, and popup fixtures with:

```bash
cd browser-extension && pnpm test && pnpm build && pnpm build:firefox
```

`pnpm test` builds clean Chrome, Firefox, and Edge MV3 targets before running
the manifest and behavior fixtures. WXT 0.21.4 accepts the Edge target without
an additional production dependency, so the Edge build is part of that gate.

`pnpm test:e2e` is deliberately a small installed-browser smoke test. It never
downloads a browser: point it at an already-installed Chrome or Firefox binary
and a running local WebDriver service through the `PRINTSTASH_EXTENSION_*`
environment variables documented in `wdio.conf.ts`. Firefox also needs the
locally built XPI and that profile's resolved `moz-extension://…/popup.html`
URL, because Firefox assigns the origin UUID at installation time.

The capture boundary has a separate CI-only Vitest harness:
`pnpm test:real-backend-capture`. CI provisions a throwaway local backend,
creates a fresh owner and browser pairing, and runs the production adapter and
transport through slot creation, raw PUT, and finalize. The harness requires
`PRINTSTASH_EXTENSION_CAPTURE_BASE_URL` and
`PRINTSTASH_EXTENSION_CAPTURE_SETUP_TOKEN`; missing provisioning fails the
test. The loaded-browser WDIO job remains a separate popup/manifest smoke
test and does not need Playwright browser downloads.
