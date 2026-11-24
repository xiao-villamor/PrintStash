# PrintStash

This Manifest V3 Chrome, Edge and Firefox extension recognizes individual MakerWorld, Printables, Thingiverse, MyMiniFactory,
Cults, Thangs, Creality Cloud and MakerOnline publications, Printables collections,
and direct model/archive file
URLs, then sends them to the Pending Imports inbox of a self-hosted PrintStash
instance.

## Chrome Web Store package

Run `pnpm install --frozen-lockfile && pnpm package:chrome` to validate the
extension and generate `.output/printstash-browser-extension-0.14.0-chrome.zip`
(the filename follows `package.json`). The ZIP contains the production extension
with its manifest at the root. Chrome and Edge exclude Firefox-specific metadata.

GitHub Actions also generates the ZIP on pull requests, pushes to `main`, release
CI, nightly runs and manual runs. Open **Actions → CI**, select the run, then
download `printstash-browser-extension-<version>-chrome.zip` from **Artifacts**.
The **Browser extension** job publishes it only after its checks pass. This is
the extension ZIP itself, ready to upload to Chrome Web Store, and is retained
for 30 days. Use **Run workflow** on `main` to generate a fresh package on demand.

The [publishing guide](CHROME-WEB-STORE.md) includes listing copy, permission
justifications, privacy disclosures and reviewer instructions. **Help & privacy**
in the popup opens the packaged help and privacy policy without a server.
Use the [public extension privacy policy](https://www.printstash.org/en/extension-privacy/)
for the store's privacy-policy URL; the site also provides a Spanish version.

## Install

1. In PrintStash, create a browser pairing code in **Settings → Imports**.
2. Build it with `pnpm install && pnpm build`. In Chrome, open
   `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**,
   and select `browser-extension/.output/chrome-mv3`. For Firefox, run
   `pnpm zip:firefox` and load the local XPI temporarily from
   `about:debugging`.
3. Open a supported source: an individual MakerWorld model (while signed in),
   a publication from another provider listed above, a Printables collection,
   or a direct model or archive file URL. Click the extension, enter the PrintStash URL and pairing
   code, then choose **Pair browser**.
4. Confirm that the popup shows **Connected** with the expected vault hostname
   and username, then choose **Send to Pending Imports**.
5. Review the detected files in **Pending Imports** before completing the
   import.

### Capture panel and recovery

On an individual MakerWorld, Printables, Thingiverse, MyMiniFactory, Cults,
Thangs, Creality Cloud or MakerOnline publication, use **Enable panel on this
site** in the popup to grant that site's permission. **Allow supported
marketplaces** enables the panel on every supported site during setup. The
panel shows file and image choices. Open the toolbar popup for History and Settings. History
lists previous captures of the publication. Hide and reopen the panel without
stopping a transfer.
Reloading or closing the tab interrupts browser work: return to the same page,
select the same files, and confirm to recover verified uploads before staging
expires. Recovery is per file, not per byte; expired staging requires downloading
again. Older servers retain the existing non-resumable transport.

Files download and transfer sequentially. Finishing transfer opens the specific
Pending Import for review; it does not mean Models have finished importing.
Review lists every result and its error, including partially completed imports.
Open **Collection, tags and cover** to suggest an editable collection, comma-separated
tags and an explicitly selected JPEG, PNG or WebP (up to 15 MiB). These are review
suggestions, not a physical storage destination. Recovery preserves the first
suggestions; change them in Pending Imports. A cover is normalized onto each
resulting Model's source without changing model bytes. If a reload interrupted its
upload, reselect the same cover; verified model uploads are retained. Cover bytes
and collection labels are not saved in extension storage.

Choose **Review files and images** to load the marketplace selection before
sending anything. All detected photos start selected. Toggle individual thumbnails
or use **Deselect all** to import files only. Use **Gallery cover** to choose
which selected image appears first. Up to 64 JPEG, PNG, WebP or GIF
images can be retained, at 15 MiB per image. GIFs become a still image of the first
frame. The first selected image is the default cover. Cards and Model
previews retain the rest of the gallery, and **Edit cover** can select another
image without discarding the others. An existing chosen cover is preserved.

Gallery support requires a server advertising `resumable_gallery`; older servers
ask you to deselect the gallery or upgrade, rather than silently dropping images.
After interruption, select the same files and images to recover confirmed slots.
Only opaque image identifiers and filenames persist in extension storage, never
image URLs, cookies or bytes. If the provider gallery cannot be read, the panel
says so and allows file-only capture. Provider markup and lazy loading can limit
which photos are detected; Printables uses its explicit publication image list.

Download progress uses a percentage only when the decoded file size is known.
Upload progress counts bytes sent; reaching 100% still waits for server confirmation.
The final import remains a separate action in Pending Imports.
MakerWorld requires signing in on its own site. PrintStash receives model bytes
and allowlisted metadata, never marketplace cookies. Download blocks, challenges
and unavailable sessions may still require action on the provider's page or
manual file attachment.

Provider adapters use the current source page and explicitly selected files:

- MyMiniFactory reads native file forms and downloads from the observed `dl4.myminifactory.com` host. Other CDN hosts require validation before enabling them.
- Cults resolves its native free-download form inside the source page, then transfers the archive from `download.cults3d.com`. It never submits a paid checkout. Creator and published license terms remain literal; use the source page for purchases and manual attachment when automatic resolution is unavailable.
- Thangs transfers the selected native archive. PrintStash expands supported mesh entries using its existing archive safety checks.
- Creality Cloud selects a print-profile package; the current adapter supports the observed USA download region.
- MakerOnline selects one package per print profile. A source-site slider or other verification must be completed manually; hide the panel while doing so. File access is requested before starting verification.

These adapters do not guarantee access to every publication, paid entitlement,
regional CDN, or changed provider interface. Sign in on the provider's own page.
Temporary download URLs remain in memory. Neither a challenge solver nor copied
marketplace cookies is a production dependency.

Compatible servers retain MakerWorld profile identity, authorship, printer,
materials, plate count and explicit time/filament estimates when supplied.
Unavailable facts stay unknown. These estimates do not create print history or
mark a Revision as recommended. Related profiles link to their existing Models
and original Artifacts; Models are not merged.

### Verify a refreshed Chrome popup

After rebuilding, confirm Chrome is loading the exact unpacked directory
`browser-extension/.output/chrome-mv3`, then click **Reload** for PrintStash on
`chrome://extensions`. Close and reopen the popup and confirm its header shows
the current SemVer release from `package.json` (for example, **Version 0.12.1**).
If the version is stale, Chrome has a different unpacked directory or an older
build loaded: use **Load unpacked** to select
`browser-extension/.output/chrome-mv3` again, click **Reload**, and reopen the
popup before testing capture behavior.

For faster setup, open **Settings → Imports** in PrintStash and choose **Create
pairing code** in the Paired browsers card. Then enter that code in the
extension. The one-time code expires after five minutes and locks after five
failed exchange attempts. Chrome still asks for explicit approval before
granting a new vault host permission.

With **Allow supported marketplaces** on (the default), that one Chrome prompt
also covers every supported marketplace and its download and image hosts, so
imports and the on-page button never ask again. Turned off, each marketplace
asks once, at its first import click, for all of its hosts together. Chrome
closes the toolbar popup when it shows that prompt: reopen the popup after
choosing **Allow** and it finishes connecting. Until then the attempt, with its
one-time pairing code, is held only in the browser's memory, for two minutes.

Server addresses:

- Loopback, private LAN addresses (`10.x`, `172.16–31.x`, `192.168.x`),
  Tailscale and other `100.64.0.0/10` addresses, and names ending in `.local`,
  `.lan`, `.home`, `.home.arpa`, `.internal`, `.intranet` or `.localdomain`
  (or with no dot at all) are LAN servers. Typed without a scheme, they are
  tried over HTTP first, then HTTPS.
- Any other name means HTTPS unless `http://` is typed. Plain HTTP to a server
  outside the LAN sends the pairing code and browser key unencrypted, so the
  popup asks for confirmation first.
- A redirect to HTTPS on the same host is followed and saved.
- A server behind a sign-in proxy (Cloudflare Access, Authelia, Authentik,
  oauth2-proxy and similar) works once you sign in to it in a normal tab: the
  extension sends the browser's cookies for the server's host, while PrintStash
  itself only accepts the extension's own Bearer credential. When the proxy
  answers instead of PrintStash, the popup says so and offers **Open sign-in
  page**. Behind such a proxy, connect with a pairing code; the API key method
  signs in without cookies.
- An HTTPS server that cannot be reached may use a self-signed certificate:
  **Open server in a tab**, accept the certificate, then connect again.
- Uploads go in parts when the server supports it, so a proxy's request-size
  limit (nginx defaults to 1 MiB, Cloudflare to 100 MB) is no ceiling: a part
  the proxy refuses is resent at half the size, down to 256 KiB.

The manifest grants network access only to loopback addresses up front; every
other server and marketplace host is an optional permission granted in Chrome's
prompt.

The extension verifies the public PrintStash health endpoint before pairing.
One selected model capture may include several files; PrintStash reserves their
bytes separately but counts the selection as one active Pending Import. If a
transfer fails after slots are created, the extension requests cleanup of that
unfinished capture. A capacity error in the popup distinguishes a full staging
area from a staging directory whose free space cannot be measured.
The one-time code is exchanged for an opaque browser-only credential; the code
is never retained, and PrintStash stores only its hash. Local extension storage
contains only the vault URL and that device credential—not a username, API key,
or access token. Revoking the browser in PrintStash stops future imports.
**Edit → Disconnect** removes the device credential and vault host permission
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
protections. For Thingiverse model pages, the extension lists the individual
files rendered by the active page and transfers only the files the user checks.
If Thingiverse requires a browser check or changes the page, the same metadata
draft remains available for a manually downloaded file.
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

Finalizing a capture prepares it for review; importing writes to managed Vault
storage. The selected collection organizes Models and does not change where
their bytes are stored. Mounted Library write-back is available separately in
the Files/Bulk upload workflow. External Library originals are not included in
Vault backups. Read-only S3, WebDAV and
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
4. Rebuild and reload the extension only when the popup release version is stale;
   storage-provider changes alone do not require an extension rebuild.

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

`pnpm test:e2e:live` additionally checks the two real regression pages for
MakerWorld model `1574312` and Thingiverse thing `7401604`. These checks are
opt-in because provider uptime and anti-bot challenges are external state. To
require MakerWorld's signed-package check, set
`PRINTSTASH_MAKERWORLD_REQUIRE_DOWNLOAD=1` and point
`PRINTSTASH_EXTENSION_USER_DATA_DIR` at a dedicated signed-in Chrome test
profile. Set `PRINTSTASH_EXTENSION_HEADLESS=0` if an interactive browser check
is required. Never use a daily browsing profile or commit its cookies.

The capture boundary has a separate CI-only Vitest harness:
`pnpm test:real-backend-capture`. CI provisions a throwaway local backend,
creates a fresh owner and browser pairing, and runs the production adapter and
transport through slot creation, raw PUT, and finalize. The harness requires
`PRINTSTASH_EXTENSION_CAPTURE_BASE_URL` and
`VAULT_SETUP_MODE=trusted_network` on the isolated backend; missing provisioning fails the
test. The loaded-browser WDIO job remains a separate popup/manifest smoke
test and does not need Playwright browser downloads.
