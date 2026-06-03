# Security Policy

PrintStash is designed for self-hosted networks. Please be careful before exposing
it directly to the public internet.

## Reporting a Vulnerability

Please do not open a public issue for security reports.

Use GitHub's private vulnerability reporting if it is available on the repository,
or contact the maintainer privately through GitHub with:

- A short description of the issue
- Steps to reproduce
- Affected configuration, if known
- Whether credentials, file access, printer control, or remote code execution is
  involved

## Supported Versions

Security support follows tagged stable releases. The currently supported stable
line is:

| Version | Supported |
| --- | --- |
| `1.0.x` | Yes |
| `< 1.0` | No |

The `main` branch is development work and may contain unreleased fixes or
regressions. If you report a vulnerability, include the exact tag or commit you
are running.

## Deployment Notes

- Change `VAULT_API_KEY` and `VAULT_JWT_SECRET` before use.
- Prefer a reverse proxy with TLS if the UI is reachable outside your LAN.
- Do not publish printer access codes, Moonraker API keys, database files, or
  backups.
- Treat uploaded G-code as sensitive if it reveals customer work, private models,
  network paths, printer names, or material usage.
