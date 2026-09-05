# First use from the browser

This guide describes the Unreleased source build. Version 0.13.0 images retain
the previous setup flow until a new minor release is published.

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

## Access addresses and proxies

Initial registration accepts localhost, private IP addresses, and names ending in
`.localhost`, `.local`, or `.home.arpa`. Set `VAULT_SETUP_ALLOWED_HOSTS` to a
comma-separated list of additional exact hostnames when needed, without scheme or
port. The browser Origin must match the request Host and scheme, including the
port. Preserve Host through proxies; do not treat a Docker proxy's private address
as evidence that a caller belongs to your trusted network.

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
