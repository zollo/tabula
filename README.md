# Tabula

A self-hosted **social media archiver**. Pick the accounts you care about on
**X (Twitter), Bluesky and Reddit**, choose how often to check them, and Tabula
keeps a searchable archive of everything they post, including their history from
before you started tracking. Browse it in the web UI or export it as **JSON** or
**PDF**.

Tabula shares its stack and conventions with Trove and Azuregos (FastAPI +
React/Vite + Postgres, Celery worker, Helm chart, TrueNAS compose), dressed in
red and black. The UI is built for phones first.

## Concepts

| Thing | What it is |
|---|---|
| **Tracked account** | A user's subscription to one social media account, with their own scan frequency (15 minutes to weekly) and a pause switch |
| **Social account** | The account on the platform. Shared: if two users track the same account there is one archive, scanned at the shorter of their intervals |
| **Post** | One archived post, reply, repost, quote or Reddit comment: text, time, link, media URLs, engagement metrics, and the platform's raw payload |
| **Backfill** | When an account is first tracked, Tabula pages back through its history in the background (resumable, capped by an admin setting) |
| **Scan** | The scheduled check for new posts |

## Personas & permissions

- **User** — tracks accounts, browses and searches their archives, exports them,
  changes frequency, pauses or stops tracking. Sees only accounts they track.
- **Admin** — everything a user can do, for every account, plus **Users**
  (create accounts, enable sign-ups, change roles, deactivate, set passwords) and
  **System settings** (sign-up on/off, enabled platforms, scan limits, backfill
  cap, platform API credentials).

People can **sign up** themselves (with a password or Google) while sign-up is
on, but they can only browse until an **admin enables** them. Accounts an admin
creates can track straight away. The first admin is created on startup from
`BOOTSTRAP_ADMIN_*`.

## Platforms

| Platform | How | Credentials | History reach |
|---|---|---|---|
| **Bluesky** | Public AppView (`app.bsky.feed.getAuthorFeed`) | None | Full history |
| **Reddit** | OAuth API, app-only token (`/user/{name}/overview`), or [public pages](#reddit-without-a-login) with no login | A "script" app from [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps): `REDDIT_CLIENT_ID` / `_SECRET`; none for public pages | About 1,000 most recent items (Reddit's listing limit) |
| **X** | API v2 (`/2/users/{id}/tweets`) | App bearer token, `X_BEARER_TOKEN`. Reading timelines needs a **paid X API tier** | 3,200 most recent posts (X's limit) |

Credentials can come from environment variables or be saved by an admin under
**Settings → Platform connections**, where they're stored encrypted (with a key
derived from `SECRET_KEY`) and never shown again. A saved value overrides the
environment one.

## Architecture

```
          ┌──────────── browser (React SPA) ───────────┐
          │  nginx (frontend image)                     │
          │   /           → SPA                         │
          │   /api, /docs, /openapi.json ──┐            │
          └────────────────────────────────┼────────────┘
                                           ▼
                            FastAPI (backend image) ──► Postgres
                                  │ queue                ▲  (users, settings,
                                  ▼                      │   accounts, posts)
       Celery beat ──► Redis ──┬──► worker ──────────────┤──► X / Bluesky / Reddit APIs
                               └──► browser worker ──────┘──► x.com / reddit.com (Chromium)
```

- **backend** — FastAPI, SQLAlchemy 2 (async), Alembic. Stateless; scales
  horizontally.
- **beat** — the scheduler. Every minute it queues accounts due for a scan and
  any unfinished backfill. Run exactly one.
- **worker** — the backend image running Celery for API-based collection (the
  `default` queue). Scale freely.
- **browser-worker** — the `tabula-browser` image (backend + Playwright +
  Chromium) for platforms an admin switched to browser collection (the `browser`
  queue). See [Browser collection](#browser-collection).
- **Redis** — the Celery broker only. Nothing durable: due work is rediscovered
  from Postgres, so Redis can restart or lose its data freely.
- **frontend** — React + Vite + TypeScript served by nginx, which proxies the
  API so everything is same-origin.
- **Postgres** — the only stateful piece.

## Quick start (Docker)

```bash
make up          # copies .env.example to .env if missing, then docker compose up --build
```

Open http://localhost:8080 and sign in as `admin@example.com` /
`change-me-please` (from `.env`). Bluesky works with no setup; add X or Reddit
credentials under **Settings**. The API is on http://localhost:8000 (`/docs`).

## Browser collection

X and Reddit can be collected with a headless browser instead of their APIs
(**Settings → Collection sources**, per platform). On X this needs no paid API
and reaches further back: it walks a "Latest" search for `from:<handle>` with a
`max_id` cursor instead of the profile timeline's ~3,200-post limit. On Reddit
it reaches the same ~1,000 items as the API.

- **Saved logins.** Run `python backend/app/tools/capture_session.py x
  x-session.json` on your own computer (needs `pip install playwright` and
  `playwright install chromium`), sign in, and upload the file under Settings.
  X needs a signed-in login; Reddit also works signed out. Logins are stored
  Fernet-encrypted and never returned by the API.
- **Pacing.** Each browser task leases one login (never shared by two browsers),
  waits *Seconds between pages* between page loads, and stops at *Pages per login
  per day*. A rate limit cools the login down for 15 minutes; a login wall or
  challenge parks it as *Needs a new login*. Tabula doesn't try to get around
  either.
- **Scaling.** Throughput ≈ logins × pages per login. Add logins, then add
  browser-worker processes (`BROWSER_WORKER_CONCURRENCY`, more replicas, or KEDA
  on the `browser` queue in the Helm chart) to match.
- **Fragility and terms.** It reads the JSON the sites' own web apps load, which
  changes without notice: when it does, scans report "didn't return any data"
  until the parser is updated. Automated access is against X's and Reddit's
  terms, and accounts used this way may be locked or suspended.

## Reddit without a login

Pick **Public pages (no login)** for Reddit under **Settings → Collection
sources** to collect posts and comments with no API app, token, account or
browser at all. The regular API worker reads the same public `.json` views that
reddit.com serves signed-out visitors (`/user/<name>/about.json` and
`/user/<name>/overview.json?sort=new`, 100 items a page) and parses them like
the API's responses. It reaches the same ~1,000 most recent items.

- **Pacing.** Reddit allows about 10 unauthenticated requests a minute per IP,
  and all of Tabula's workers usually share one IP, so requests are paced
  globally through the database: each claims the next slot (*Seconds between
  public requests*, default 10, minimum 6) and waits for it. When the queue of
  slots is more than a minute long, scans and backfills step aside and are picked
  up again by the scheduler when their slot comes round instead of holding a
  worker.
- **Backing off.** Tabula honours Reddit's `x-ratelimit-remaining`/`-reset`
  headers and `429`s by pausing every worker until the window resets, and an
  HTML `403` ("blocked") pauses public requests for 15 minutes. Reddit blocks
  many datacenter IPs outright; a home connection usually works.
- **User agent.** Requests send `REDDIT_USER_AGENT` (or the saved value);
  Reddit throttles generic agents hardest, so keep it descriptive.
- **Terms.** Public pages need no account, but Reddit's terms still govern
  automated access to them.

## Local development without Docker

```bash
# Postgres and Redis somewhere reachable, then:
cd backend
pip install ".[dev]"
export POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0
alembic upgrade head
uvicorn app.main:app --reload                                      # http://localhost:8000
celery -A app.workers.celery_app.celery_app beat -l info            # the scheduler
celery -A app.workers.celery_app.celery_app worker -Q default -l info  # scans and backfills
# Browser collection only: pip install ".[browser]" && playwright install chromium
celery -A app.workers.celery_app.celery_app worker -Q browser -l info

cd ../frontend
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev   # http://localhost:5173
```

## Testing

```bash
cd backend
ruff check .
pytest -q                 # SQLite, no Redis, no network: platforms are faked
TABULA_TEST_DATABASE_URL=postgresql+asyncpg://tabula:tabula@localhost:5432/tabula pytest -q

cd ../frontend
npm run build && npm test
```

## REST API

Interactive docs at `/docs` (Swagger UI) and `/redoc`; the spec at
`/openapi.json`, also committed at [`docs/openapi.json`](docs/openapi.json) and
attached to every release. Authenticate with `Authorization: Bearer <token>`
from `POST /api/auth/login`.

| Operation | Purpose |
|---|---|
| `GET /api/options` | Platforms you can track and the scan-interval limits |
| `GET/POST /api/trackings` | Your tracked accounts / track a new one (`platform`, `handle` or profile URL, `scan_interval_minutes`) |
| `PATCH/DELETE /api/trackings/{id}` | Change frequency, pause, or stop tracking (the archive is kept) |
| `POST /api/trackings/{id}/scan` | Scan now |
| `GET /api/accounts/{id}/posts` | Browse: `q`, `kind`, `since`, `until`, `limit`, `offset` |
| `GET /api/accounts/{id}/export?format=json\|pdf` | Download the archive (same filters) |
| `GET/PATCH /api/settings` | System settings (admin) |
| `GET/POST/PATCH/DELETE /api/users` | User management (admin) |

## Configuration

All settings are environment variables; see [`.env.example`](.env.example).
Most day-to-day settings (sign-up, platforms, limits, credentials) are edited
by admins in the UI instead.

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | `change-me` | Signs sessions and encrypts saved credentials. **Set a long random value**; with `TABULA_ENV=production` the backend refuses a placeholder. Rotating it invalidates saved platform credentials. |
| `POSTGRES_*` / `DATABASE_URL` | `tabula` / derived | Database connection |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `CORS_ORIGINS` | `http://localhost:5173` | Browser origins allowed to call the API cross-origin |
| `TABULA_PUBLIC_URL` | `http://localhost:8080` | Public URL (Google sign-in redirects) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Web session lifetime |
| `ALLOW_REGISTRATION` | `true` | Initial value of the sign-up setting |
| `BOOTSTRAP_ADMIN_EMAIL` / `_PASSWORD` | `admin@example.com` / generated | First admin, created if missing. With no password set, a random one is printed in the backend log on first start. |
| `X_BEARER_TOKEN` | empty | X API v2 app bearer token |
| `REDDIT_CLIENT_ID` / `_SECRET` / `_USER_AGENT` | empty / empty / `tabula/1.0 (archiver)` | Reddit app-only OAuth |
| `BLUESKY_APPVIEW_URL` | `https://public.api.bsky.app` | Bluesky AppView |
| `SCHEDULER_TICK_SECONDS` | `60` | How often the scheduler looks for due scans |
| `BACKFILL_PAGES_PER_RUN` | `5` | History pages per backfill task before it re-queues itself |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | empty | Google OAuth client. Google sign-in is **off** unless both are set. |
| `GOOGLE_REDIRECT_URI` | `$TABULA_PUBLIC_URL/auth/google/callback` | Override only if Tabula is reached on a different URL |

## Google sign-in

With a Google OAuth client configured, the sign-in and sign-up pages show
**Sign in with Google**, and every user gets a **Google sign-in** card on the
Account page to link or unlink their Google account.

How accounts are matched:

- A Google account that is **linked** signs straight into its Tabula account
  (user or admin).
- A Google account that is **not linked** and whose email is **new** to Tabula
  creates a user account, if sign-up is on (System settings) and Google has
  verified the email. These accounts have no password and, like any sign-up,
  can't track anyone until an admin enables them.
- If the Google email **already belongs** to a Tabula account, sign-in is
  refused: the owner signs in with their password once and links Google from
  Profile/Settings. Tabula never merges accounts by email alone, so an existing
  account can't be taken over through a Google account with the same address.
- Unlinking requires a password, so nobody locks themselves out. An admin can
  set one for a Google-only user from Users.

### Setting up the Google Cloud OAuth client

1. In the [Google Cloud console](https://console.cloud.google.com/), create or
   pick a project.
2. **Google Auth Platform → Branding** (formerly *OAuth consent screen*): set
   the app name (e.g. Tabula), support email and, under *Authorized domains*,
   your Tabula domain (e.g. `example.com`).
3. **Audience**: choose *External* (any Google account) or *Internal* (only
   your Google Workspace). An External app in *Testing* only lets listed test
   users sign in; click **Publish app** to open it up. Tabula only asks for the
   `openid`, `email` and `profile` scopes, which need no Google verification.
4. **Clients → Create client** → *Web application*:
   - *Authorized JavaScript origins*: your public URL, e.g.
     `https://tabula.example.com`.
   - *Authorized redirect URIs*: `https://tabula.example.com/auth/google/callback`
     (the `TABULA_PUBLIC_URL` plus `/auth/google/callback`, exactly).
5. Copy the **Client ID** and **Client secret** into `GOOGLE_CLIENT_ID` and
   `GOOGLE_CLIENT_SECRET` and restart the backend. The login page picks the
   change up automatically.

Google only accepts `https://` redirect URIs on a public domain, plus
`http://localhost` for development. A LAN URL such as
`http://truenas.local:30082` won't work; put Tabula behind HTTPS on a real
domain (a reverse proxy or tunnel) to use Google sign-in there. For local
development add `http://localhost:8080/auth/google/callback` (Docker) or
`http://localhost:5173/auth/google/callback` (Vite, with
`TABULA_PUBLIC_URL=http://localhost:5173`).

Under the hood this is the OpenID Connect authorization-code flow with PKCE,
handled by the backend (`app/services/google_auth.py`) so the client secret
never reaches the browser. The flow is stateless (a signed, 10-minute `state`),
so it works with any number of backend replicas.

## Deploying to TrueNAS SCALE

[`deploy/truenas/`](deploy/truenas/) has a compose file that uses the published
GHCR images, publishes a single port (30082) for the UI and API, and keeps
Postgres on a host dataset for snapshots. See
[deploy/truenas/README.md](deploy/truenas/README.md).

## Deploying to Kubernetes

The Helm chart in `charts/tabula` deploys the backend (with an
`alembic upgrade head` init container), the scan worker (one replica, it runs
the scheduler), Redis, the frontend, Postgres (StatefulSet + PVC) and an Ingress
that sends `/api`, `/docs`, `/redoc` and `/openapi.json` to the backend and
everything else to the SPA. Every release
publishes it to GHCR with the same version as the images:

```bash
kubectl create namespace tabula
kubectl -n tabula create secret generic tabula-secrets \
  --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=BOOTSTRAP_ADMIN_PASSWORD="$(openssl rand -hex 12)"
  # optional: Google sign-in (with config.GOOGLE_CLIENT_ID) and platform credentials
  # --from-literal=GOOGLE_CLIENT_SECRET=... --from-literal=X_BEARER_TOKEN=... \
  # --from-literal=REDDIT_CLIENT_SECRET=...

helm install tabula oci://ghcr.io/zollo/charts/tabula -n tabula \
  --set existingSecret=tabula-secrets \
  --set ingress.enabled=true --set ingress.hosts[0]=tabula.example.com \
  --set config.CORS_ORIGINS=https://tabula.example.com \
  --set config.TABULA_PUBLIC_URL=https://tabula.example.com
  # --set config.GOOGLE_CLIENT_ID=1234-abc.apps.googleusercontent.com
```

See `charts/tabula/values.yaml` for external Postgres, resources, and
`postgresql.backup` (a nightly `pg_dump` CronJob to its own PVC).

## CI / releases

`.github/workflows/ci.yml` runs on every PR and every push to `main`:

1. **backend** — ruff, pytest (SQLite), and a check that `docs/openapi.json`
   matches the code
2. **backend-postgres** — `alembic upgrade head`, `alembic check`, pytest on Postgres
3. **frontend** — type-check, build, vitest
4. **smoke** — boots the whole compose stack and checks health, the nginx
   proxy, `/openapi.json`, that the API needs auth, and that the worker is up
5. **chart-lint** — `helm lint` + `helm template`

On a **merge to `main`** (after the above pass):

6. **version** — computes the next **semantic version** from the squash commit
   (Conventional Commits: `feat:` → minor, `fix:` → patch, `!` / `BREAKING
   CHANGE` → major; default patch; first release is `v1.0.0`), tags it and
   creates a GitHub release. PR titles drive this (see
   [CONTRIBUTING.md](CONTRIBUTING.md)).
7. **api-spec** — attaches `tabula-openapi-X.Y.Z.json` to the release.
8. **images** — builds `ghcr.io/zollo/tabula-backend` and `-frontend` tagged
   `latest`, `X.Y.Z`, `X.Y`, `sha-<short>`, signs them with cosign (keyless) and
   pushes build-provenance attestations.
9. **chart** — packages the Helm chart at the release version and pushes it to
   `oci://ghcr.io/zollo/charts/tabula`.

### Verifying an image

```bash
IMAGE=ghcr.io/zollo/tabula-backend:latest
cosign verify "$IMAGE" \
  --certificate-identity-regexp "https://github.com/zollo/tabula/.github/workflows/ci.yml@.*" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
gh attestation verify "oci://$IMAGE" --repo zollo/tabula
```

## Project layout

```
backend/            FastAPI app, Celery worker, Alembic migrations
  app/
    api/routes/      auth, users, settings/options, trackings + accounts/posts/export, health
    core/            security (JWT, passwords, credential encryption), errors, export (JSON/PDF)
    models/          SQLAlchemy models
    platforms/       X, Bluesky and Reddit clients behind one interface
    schemas/         Pydantic schemas
    services/        user, settings, tracking, scan and post services
    workers/         Celery app, tasks (scheduler, scan, backfill), dispatch from the API
    migrations/      Alembic
frontend/           React + Vite SPA (mobile-first, red/black)
  src/
    api/             typed fetch client
    components/      layout + tab bar, avatars, Google sign-in
    pages/           dashboard, track, account archive, account; admin/* pages
charts/tabula/      Helm chart (published to oci://ghcr.io/zollo/charts/tabula)
deploy/truenas/     TrueNAS SCALE compose
docs/               Published API contract (OpenAPI spec)
```

## License

Provided as-is for personal use. Add a license here if you share it.
