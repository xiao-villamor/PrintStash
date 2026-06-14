---
title: Installation
description: Bring up PrintStash with Docker Compose and create the first admin account.
---

PrintStash ships as a Docker Compose stack. Docker and Docker Compose are the
only requirements for a standard install.

## Quick start

```bash
git clone https://github.com/xiao-villamor/PrintStash.git
cd PrintStash

cp .env.example .env
# Edit .env and change VAULT_JWT_SECRET.

docker compose up -d --build
```

Once the stack is up, open:

| Service      | URL                                      |
| ------------ | ---------------------------------------- |
| Web UI       | http://localhost:3000                    |
| API docs     | http://localhost:8000/docs               |
| Health check | http://localhost:8000/api/v1/health      |

## First launch

On first launch the web UI walks you through a setup wizard at
`http://localhost:3000/setup` and creates the first admin account. **There is no
default username or password** — the account you create during setup is the
first one that exists.

After signing in you can add printers, upload models, and create API keys from
**Settings**.

## Before you go to production

- Set a strong, unique `VAULT_JWT_SECRET`. Treat it as a credential.
- Decide on storage: SQLite + local disk is the default; switch to Postgres
  and/or S3/R2 if you need them (see [Configuration](/PrintStash/getting-started/configuration/)).
- Plan backups. See the project's disaster-recovery notes for backup/restore.

## Upgrades

Pull the latest images (or rebuild) and restart the stack:

```bash
git pull
docker compose up -d --build
```

Database migrations run automatically on startup. Review the project's upgrade
guidance before major version bumps.
