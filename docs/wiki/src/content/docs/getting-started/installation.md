---
title: Installation
description: Bring up the Docker Compose stack and create your first admin account.
---

PrintStash ships as a Docker Compose stack. If you have Docker and Docker Compose
installed, you don't need Python, Node, or anything else on the host — it all
runs in containers.

## Quick start

```bash
git clone https://github.com/xiao-villamor/PrintStash.git
cd PrintStash

cp .env.example .env
# Open .env and change VAULT_JWT_SECRET to a long random string.

docker compose up -d --build
```

The first build pulls base images and compiles the frontend, so give it a few
minutes. When it settles, you'll have two containers running — `printstash-api`
and `printstash-frontend` — plus a handful of named volumes for your data.

| Service      | URL                                  | What it is                       |
| ------------ | ------------------------------------ | -------------------------------- |
| Web UI       | http://localhost:3000                | The app                          |
| API docs     | http://localhost:8000/docs           | Interactive Swagger reference    |
| Health check | http://localhost:8000/api/v1/health  | Liveness + component readiness   |

The frontend container runs nginx, which serves the static build and proxies
`/api/v1` and WebSocket traffic to the API on the same origin. That's why the UI
and API can both live behind `localhost` without CORS gymnastics.

## First launch

Open the UI and you'll be sent to the setup wizard at
`http://localhost:3000/setup`. It walks you through:

1. Creating the **first admin account** (username + password).
2. Choosing a storage backend — `local` disk or S3-compatible.
3. Confirming data directories and backup retention.
4. Optionally wiring up a separate S3/R2 bucket for backups.

There is **no default username or password**. The account you create in the
wizard is the first one that exists — which is deliberate, because a shipped
default credential is exactly the kind of thing that ends up exposed on the
public internet. If the wizard won't complete, fix the wizard; there's nothing to
fall back on.

Once you're in, head to **Settings** to mint an API key, then add printers and
start uploading.

## Optional services

The Compose file ships Postgres and MinIO behind profiles, so they stay off
unless you ask for them:

```bash
# Run with Postgres instead of SQLite
docker compose --profile postgres up -d

# Run a local MinIO target for S3-compatible storage testing
docker compose --profile s3 up -d minio
```

Postgres listens on `5432`, and MinIO on `9000` (API) / `9001` (console). You
only need these for larger or multi-user installs — see
[Configuration](/PrintStash/getting-started/configuration/).

## Before you expose it

PrintStash is built for a **trusted home network**. A few things to settle before
it's reachable from anywhere beyond your LAN:

- Set a strong, unique `VAULT_JWT_SECRET`. Treat it like a password — anyone who
  has it can forge valid tokens.
- Put it behind a reverse proxy with TLS. Don't port-forward the raw containers.
- Decide on storage. SQLite + local disk is the default and the best-tested path;
  switch to Postgres and/or S3 only if you actually need them.
- Plan backups *before* you need them. See
  [Backup & restore](/PrintStash/guides/backup-and-restore/).

## Troubleshooting first boot

- **Setup wizard never appears / 502 from the UI.** The API probably hasn't
  finished migrating yet. `docker compose logs -f api` and wait for it to report
  the server is up; migrations run automatically on startup.
- **"Connection refused" on port 3000 or 8000.** Something else is bound to the
  port, or the container failed to start. Check `docker compose ps`.
- **Health check shows a component unhealthy.** Hit
  `http://localhost:8000/api/v1/health` directly — it breaks readiness out by
  database, storage, backup, and printer providers, so you can see which one is
  unhappy.

For upgrading an existing install, see
[Upgrading](/PrintStash/guides/upgrading/).
