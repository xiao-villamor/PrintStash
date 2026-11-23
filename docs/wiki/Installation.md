# Installation

Follow the [repository Quick Start](https://github.com/xiao-villamor/PrintStash/blob/main/README.md#quick-start)
for a fresh install with Docker Compose. The default deployment runs the web
app and API in one container, with SQLite and local storage in a persistent
volume mounted at `/data`. Published images support Linux AMD64 and ARM64.

Use the Compose file and image pin for the same published release. The
[latest release](https://github.com/xiao-villamor/PrintStash/releases/latest)
lists the version. The Docker image tag omits the leading `v`.

On the default stack, open <http://localhost:3000> from the host or use the
server's address from a trusted network. The first person who completes browser
registration becomes the administrator. Keep access restricted during setup.

- [Installation options and troubleshooting](https://www.printstash.org/en/docs/getting-started/installation/)
- [First run](https://github.com/xiao-villamor/PrintStash/blob/main/docs/first-run.md)
- [Deployment and optional settings](https://github.com/xiao-villamor/PrintStash/blob/main/docs/deployment.md)
- [Upgrade guide](https://github.com/xiao-villamor/PrintStash/blob/main/UPGRADE.md)
  for existing installations. Verify a backup and keep the current Compose
  project and persistent data when upgrading.
