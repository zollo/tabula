# Tabula on TrueNAS SCALE

A Docker Compose deployment tuned for **TrueNAS SCALE (Electric Eel 24.10+)**,
which runs Docker natively.

## Why this compose is TrueNAS-friendly

- **Prebuilt images** from GHCR — TrueNAS pulls, it doesn't build.
- **One published port.** The frontend reverse-proxies `/api` to the backend,
  so the UI and the REST API are both on `http://<truenas>:30082` with no CORS
  and no baked API URL. Postgres, Redis, the API and the worker stay on the
  internal network. (30082 so it can sit next to Azuregos on 30080 and Trove on
  30081.)
- **Data on a dataset.** Postgres (the source of truth, including every archived
  post) is bind-mounted to a host dataset you can snapshot and replicate. Redis
  is only the job queue and keeps nothing on disk.

## 1. Create the dataset

In the TrueNAS UI: **Datasets → Add Dataset**, e.g. `tank/apps/tabula` (swap
`tank` for your pool). The Postgres data lands in a `postgres` subfolder created
automatically on first run.

## 2. Configure

Every setting — **including all secrets** (`SECRET_KEY`, `POSTGRES_PASSWORD`,
`BOOTSTRAP_ADMIN_PASSWORD`) — is a plain environment variable: a `.env`
file next to `docker-compose.yml` (Dockge / `docker compose`), the shell, or the
TrueNAS Custom App *Environment Variables* section.

`SECRET_KEY` is required: the stack refuses to start without it. If you leave
`BOOTSTRAP_ADMIN_PASSWORD` blank, a random password is generated on first
start and printed in the backend log (`docker logs tabula-backend`).

```bash
cp .env.example .env
openssl rand -hex 32        # SECRET_KEY
openssl rand -hex 16        # POSTGRES_PASSWORD
openssl rand -hex 12        # BOOTSTRAP_ADMIN_PASSWORD
```

Also set `TABULA_DATA` to your dataset path, `TABULA_PORT` to the host port, and
`TABULA_PUBLIC_URL` to how people (and AI tools) will reach it.

> Changing `POSTGRES_PASSWORD` after the first run won't update an
> already-initialized database. Set it before the first `up`.

## 3. Install

**Option A — Dockge / docker compose (recommended).** Put `docker-compose.yml`
and `.env` in a folder on a dataset and run `docker compose up -d`.

**Option B — TrueNAS "Custom App".** Apps → Discover Apps → **Custom App** →
*Install via YAML*. Paste `docker-compose.yml` and either replace the `${...}`
placeholders with literal values or add the variables in the app's environment
section.

## 4. First login

Open `http://<truenas-ip>:30082` and sign in with `BOOTSTRAP_ADMIN_EMAIL` and
`BOOTSTRAP_ADMIN_PASSWORD` (or the generated password from the backend log).
Under **Settings**, add the X bearer token and Reddit API credentials (Bluesky
needs none) and choose the scan limits. People can sign up from the login page,
but they can only track accounts once you enable them under **Users**.

**Google sign-in** is optional: set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
(see the main README's *Google sign-in* section). Google only redirects to
`https://` URLs on a public domain, so it needs Tabula behind HTTPS (a reverse
proxy or tunnel) with `TABULA_PUBLIC_URL` set to that URL; it won't work on a
plain `http://<truenas-ip>:30082` address.

## 5. Backups

Snapshot (and optionally replicate) the `TABULA_DATA` dataset — it holds the full
Postgres database: users, settings, tracked accounts and every archived post.
