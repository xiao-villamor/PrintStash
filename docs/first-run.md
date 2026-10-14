# First use from the browser

This guide describes the browser setup flow included since 0.14.0 and used in
0.15.0. Version 0.13.x images use the earlier console-token flow.

Start the local Compose deployment on a trusted network, open PrintStash, and
create your local administrator account. There is no console credential to copy.
The first person who completes registration becomes the administrator. Keep the
installation accessible only to people you trust during that time.

The API defaults to `VAULT_SETUP_MODE=disabled`. Local Compose files deliberately
enable `trusted_network`; production Compose leaves registration disabled. An
existing user or a previous installation marker permanently closes first-owner
registration, even after a restart or lost browser cookie.

## Your account

Choose a username and a password you can save in your password manager. The
language and theme controls are available immediately. Email is optional account
profile information; PrintStash does not send password-reset emails.

## Your files

The recommended destination uses the deployment's persistent storage volumes for
uploads and previews. Open **View location and advanced options** to inspect
paths or choose a remote provider. Use separate empty directories for managed
local storage. An existing folder of models belongs in **Library sources** in the
next step, rather than becoming the managed upload directory.

**Check storage** checks the selected destination's access. For local storage it
also reports available space when measurable. Remote checks use temporary probe
objects to verify the required access. A successful check is current evidence of
access, not a backup or a recovery guarantee. Review the account and destination,
then choose **Create my account and continue**.

The account and installation marker are committed together. If subsequent storage
preparation fails, the account remains usable. Sign in and return to the guide to
retry preparation. If the browser loses the creation response, it checks whether
registration has closed and offers sign-in instead of resubmitting creation.

## Get started

Upload files with the existing uploader, or explicitly enable Library sources and
connect an existing folder. Locations are paths visible to the API process. A
container path may differ from the host/NAS path; the guide suggests accessible
mount points and explains how to add a read-only bind mount when one is missing.
The browser cannot mount host folders. New sources have writeback disabled.

Run the first scan, inspect its results, and retry if the source was temporarily
unavailable. Open a resulting Model or your library. You can postpone this step
and resume it from Settings or an empty library. Printer connection and backups
are optional next steps. Passwords stay in memory; only non-sensitive preferences
and guide progress are saved in the browser.

## An administrator from the deployment

App-store installs (Unraid, Runtipi, CasaOS) and unattended deployments can create
the first administrator without the browser registration step:

```yaml
environment:
  VAULT_SETUP_MODE: environment
  VAULT_SETUP_ADMIN_USERNAME: admin
  VAULT_SETUP_ADMIN_PASSWORD: <at least 8 characters>
  VAULT_SETUP_ADMIN_EMAIL: admin@example.net # optional
```

At startup, an installation without an owner creates that administrator. Sign in
with those credentials. PrintStash then opens the **Your files** step, because no
storage has been chosen yet; choose it there to finish setup. The choice is checked
before it is saved, so a mistyped remote setting can be corrected. If the deployment
already selects storage through environment variables (for example
`VAULT_STORAGE_PROVIDER`), that selection counts as the choice and PrintStash only
prepares it.

These variables are consumed once. They never change an existing account, so
editing the password in an install form later does not change the account's
password; reset it under **Settings → Users** instead. Blank values are unset.
The password is never written to the logs.

`VAULT_SETUP_MODE` and the administrator variables are checked together, while
the installation has no owner:

| Mode | Administrator variables | Result |
| --- | --- | --- |
| `environment` | username and password set and valid | The administrator is created at startup; the browser cannot register |
| `environment` | missing, or below the minimums (username 3, password 8 characters) | **Misconfigured**: no administrator is created |
| `trusted_network` | not set | Browser registration from the local network |
| `disabled` | not set | No first-run path |
| `trusted_network` or `disabled` | any set | **Misconfigured**: the variables would be ignored |

A misconfigured installation keeps first ownership closed: no browser can
register and no administrator is created. The setup page names the variables to
change and both ways to fix them, and the same message is logged at startup.
Nothing becomes claimable by whoever arrives first. Once an installation has an
owner, the combination no longer matters.

## When the browser cannot register

If this browser may not create the first administrator, the setup page explains
why, names the address PrintStash saw, and lists what to change:

- **Registration is disabled** (`VAULT_SETUP_MODE=disabled`): enable
  `trusted_network`, or provision the administrator from the deployment.
- **The address is not recognised as private**: open PrintStash at its local
  network address, add the address to `VAULT_SETUP_ALLOWED_HOSTS`, or provision
  the administrator from the deployment.
- **The administrator comes from the deployment** (`VAULT_SETUP_MODE=environment`):
  restart PrintStash so it creates the administrator, then sign in.
- **The first-run settings don't fit together**: the page names the variables and
  the two fixes, as in the table above.

After changing a setting, restart PrintStash and choose **Check again**.

## Access addresses and proxies

Initial registration accepts localhost, private IP addresses, and names ending in
`.localhost`, `.local`, or `.home.arpa`. Set `VAULT_SETUP_ALLOWED_HOSTS` to a
comma-separated list of additional exact hostnames when needed, without scheme or
port. The browser Origin must match the request Host and scheme, including the
port. Preserve Host through proxies; do not treat a Docker proxy's private address
as evidence that a caller belongs to your trusted network.

Tailscale addresses are not trusted automatically. `100.64.0.0/10` is also the
range ISPs use for carrier-grade NAT, where other customers can reach a forwarded
port, and Tailscale Funnel can publish `*.ts.net` names to the internet. List your
tailnet name or address in `VAULT_SETUP_ALLOWED_HOSTS`, or provision the
administrator from the deployment.

The browser obtains a temporary HttpOnly, SameSite Strict preparation cookie and
automatically sends its anti-CSRF proof. Preparation lasts 60 minutes and can be
renewed while registration remains open. It does not reserve the account or lock
out another visitor. These controls complement the trusted deployment boundary;
they do not establish who owns a public installation.

## Beginner evaluation protocol (not yet conducted)

Recruit five beginners for the released flow and five different beginners for the
new flow, with comparable familiarity with self-hosting. Use isolated installations
with Compose already running. Give each participant a model file and a mounted
sample folder, and ask them to create an account and open their first Model.
Alternate the upload and existing-folder tasks across participants.

Record time from opening the app to opening the Model, requested/provided help,
errors encountered, and abandonment. Do not collect passwords or add product
telemetry. Observe without prompting; record assistance rather than counting an
assisted completion as independent. The initial target is four of five completing
without help. Record actual results before drawing conclusions; automated browser
tests do not substitute for this evaluation.
