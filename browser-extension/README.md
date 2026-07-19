# PrintStash Quick Capture

Load this directory as an unpacked Manifest V3 extension. Configure Vault base
URL, PrintStash username, and named API key, then use toolbar action to send current HTTP(S) page to
`POST /api/v1/inbox`.

Extension sends page URL and title only. It does not inspect page contents,
browser cookies, or site credentials. Resolution remains server-side and uses
PrintStash SSRF protections.

The helper exchanges the username and named API key for a short-lived access
token for each capture; it does not retain that token. Vault URL, username, and
named API key remain in local extension storage and are not synced through the
browser account.
