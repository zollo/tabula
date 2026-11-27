# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Tabula is a social media archiver. Users pick accounts on **X, Bluesky or Reddit** and a scan frequency; Tabula backfills each account's history, keeps scanning for new posts, and lets users browse, search and export (JSON / PDF) the archive. Admins manage users and system settings. It shares its stack and conventions with `zollo/trove` and `zollo/azuregos` (red/black theme instead of theirs).

## Commands

Full stack (Docker; `docker-compose.override.yml` is auto-merged for hot reload):
```bash
make up                      # creates .env from .env.example if missing, then compose up --build
docker compose -f docker-compose.yml up --build   # prod-like, no dev override
```

Backend (`cd backend`):
```bash
pip install ".[dev]"
ruff check .
pytest -q                                        # SQLite; no services needed (no Redis either)
TABULA_TEST_DATABASE_URL=postgresql+asyncpg://tabula:tabula@localhost:5432/tabula pytest -q   # same suite on Postgres
pytest tests/test_tracking.py::test_add_tracking_backfills_history
alembic upgrade head && alembic check            # migrations apply and match the models
python -m app.openapi_export ../docs/openapi.json   # or `make openapi`
uvicorn app.main:app --reload
celery -A app.workers.celery_app.celery_app beat --loglevel=info              # scheduler (one)
celery -A app.workers.celery_app.celery_app worker -Q default --loglevel=info   # API scans
celery -A app.workers.celery_app.celery_app worker -Q browser --loglevel=info   # needs ".[browser]"
```

Frontend (`cd frontend`):
```bash
npm install
npm run dev          # Vite on :5173; set VITE_API_BASE_URL=http://localhost:8000
npm run build        # tsc (strict, noUnused*) + vite build
npm test             # vitest
```

## Architecture

**Services:** `backend` (FastAPI, REST at `/api`), `beat` (the scheduler, exactly one), `worker` (backend image, Celery on the `default` queue), `browser-worker` (`browser` Dockerfile target / `tabula-browser` image with Playwright + Chromium, Celery on the `browser` queue), `frontend` (React/Vite SPA served by nginx), Postgres, Redis (Celery broker only, no durable data).

**Service layer.** `app/services/*` hold all logic and permission checks and raise `core/errors.TabulaError` subclasses; routes (`app/api/routes/*`) map them to HTTP via an exception handler in `main.py`. `docs/openapi.json` is the published contract (CI fails if stale; `make openapi`).

**Data model.** `SocialAccount` (platform + `external_id`, unique) is **shared**: posts belong to the account, not to a user. `Tracking` is a user's subscription with their `scan_interval_minutes` and `is_paused`. The account's effective `scan_interval_minutes` is the shortest among active trackings (`tracking_service.refresh_schedule`); `None` means nobody is tracking and it isn't scanned. Stopping tracking keeps the archive. `Post` is unique on (account, `external_id`), keeps `raw` (deferred) plus parsed `text`, `kind`, `media` URLs and `metrics`.

**Visibility.** A user sees a social account and its posts only while they track it (`tracking_service.get_account` returns 404 otherwise); admins see everything.

**Roles & approval** (`models/user.py`): `user` and `admin`. `is_active=False` blocks sign-in. `is_enabled` gates tracking: self-registered and Google sign-ups start disabled and can only browse until an admin enables them (`User.can_track`); admin-created users start enabled; admins always can. The last active admin can't be demoted, deactivated or deleted.

**Platforms** (`app/platforms/`): each client implements `resolve(handle) -> ProfileInfo` and `fetch_page(profile, cursor) -> Page` with posts **newest first**; `next_cursor` walks back in time. Bluesky uses the public AppView (no creds). Reddit uses app-only OAuth and lists only ~1,000 items per user. X uses API v2 with a bearer token (paid tier; 3,200 most recent posts). `PlatformError.retryable` (429/5xx/network) decides whether a failure is retried or marks a backfill `failed`. Always build clients with `platforms.get_client(platform, settings_service.platform_credentials(row))` (call it via the module so tests can monkeypatch it).

**Scanning** (`services/scan_service.py`, `workers/tasks.py`): beat runs `schedule_due_work` every `SCHEDULER_TICK_SECONDS`. It claims due accounts with a conditional `UPDATE … WHERE next_scan_at = <seen>` (so a scan is queued once per slot) and re-queues backfills by `backfill_touched_at`: `pending` ones untouched for 5 min, `running` ones for 30 min (claiming bumps it, so each is re-queued once). "Scan now" just sets `next_scan_at` to now (works while paused); the next tick picks it up. `scan` pages from newest until a page contains a known post (max 5 pages). `backfill` resumes from `backfill_cursor`, does `BACKFILL_PAGES_PER_RUN` pages per task, commits after each page (touching `backfill_touched_at`), re-queues itself, and stops at the end of history or `backfill_max_posts`. Each new post is inserted in a savepoint, so a scan and a backfill storing the same post concurrently skip the duplicate instead of failing. The API only queues backfills directly (`workers/dispatch.py`), best-effort: if Redis is down the request still succeeds and the scheduler catches up.

**Browser collection** (`platforms/browser/`, `services/scraper_session_service.py`): `SystemSettings.platform_sources` picks `api`, `browser` (X and Reddit only) or `public` (below) per platform (`settings_service.platform_source`). `scan_service.client_for` builds the matching client; browser clients implement the same `resolve`/`fetch_page` contract, lease a `ScraperSession` (saved Playwright storage state, Fernet-encrypted) through a `Broker`, pace page loads (`browser_page_interval_seconds`, max 300 s so it stays inside the 10-minute lease), call `Broker.start_page` before every load (re-checks the lease is still theirs, takes one page of `browser_daily_page_cap`, renews the lease; raises retryable otherwise), and release with an outcome (`ok`, `rate_limited` → 15 min cooldown, `challenged` → `needs_attention`, `error`). Always `scan_service.close(client)` when done. They never parse HTML: `PlaywrightDriver.collect` captures JSON responses matching a URL fragment while scrolling (X `SearchTimeline`/`UserByScreenName`), `get_json` loads Reddit `.json` URLs. X pages with `max_id:<oldest-1>` search cursors. Leasing re-checks every eligibility condition inside the conditional UPDATE. Browser clients only end a backfill on a recognized empty result (X `instructions`, Reddit `Listing`); errors or unknown shapes are retryable. Queues: `dispatch.queue_for(source)`; the scheduler and backfill re-queue route by the account's platform source, and a scan/backfill delivered to the wrong queue (source switched after queueing) republishes itself to the right one. Switching a platform's source restarts its unfinished backfills from the newest post (API and browser cursors don't mix), and a running backfill stops if the source changes under it. The API has no browser, so adding an account on a browser platform runs `resolve_profile` on the browser worker and waits for the result (`dispatch.resolve_in_browser`, Redis result backend, 120 s timeout). `app/tools/capture_session.py` is a standalone script users run on their own machine. Never add anti-bot evasion (CAPTCHA solving, fingerprint spoofing, proxy rotation): a challenged login is parked for an admin.

**Public collection** (`platforms/reddit_public.py`, `services/pacer_service.py`): a third source, `public` (Reddit only, `settings_service.PUBLIC_PLATFORMS`), reads Reddit's signed-out `.json` pages with httpx on the `default` queue: no credentials, no browser. Build it via `scan_service.client_for` (→ `platforms.get_public_client`, monkeypatchable). Every request first calls `Pacer.wait`, which claims the next slot in the `request_pacers` row with a conditional UPDATE (`public_request_interval_seconds` apart, shared by all workers and the API) and commits; a slot further than `max_wait` raises `Throttled(retry_at)`, which `scan` turns into `next_scan_at = retry_at` and `backfill` into `pending` (re-queued by the scheduler) rather than an error. Reddit's `x-ratelimit-*` headers, 429s and HTML 403s call `Pacer.hold` to pause everyone.

**System settings** (`models/system_settings.py`, singleton row id=1, created on first read): sign-up on/off (initial value from `ALLOW_REGISTRATION`), enabled platforms, platform sources and browser pacing, min/default scan interval, trackings per user, backfill cap, and platform credentials. Credentials default to env vars; an admin-saved value (Fernet-encrypted with a key derived from `SECRET_KEY`) wins per key; the API only ever reports each key's source (`saved`/`environment`/`unset`), never secret values. Rotating `SECRET_KEY` makes saved credentials unreadable (logged, env used instead).

**Auth:** `Authorization: Bearer` session JWT (`/api/auth/login`, stored in localStorage by the SPA). **Google sign-in** (`services/google_auth.py`, on only when `GOOGLE_CLIENT_ID`/`_SECRET` are set) is a stateless OIDC code flow with PKCE: `POST /api/auth/google/authorize` returns Google's URL plus a signed `state` JWT and the verifier, the SPA keeps them in sessionStorage, Google redirects to the SPA route `/auth/google/callback`, which posts the code to `google_login` or `google_link`. Accounts match on `users.google_sub` only, never by email; Google-only users have `hashed_password = NULL`. A bootstrap admin is created on startup from `BOOTSTRAP_ADMIN_*`.

**Exports** (`core/export.py`): `GET /api/accounts/{id}/export?format=json|pdf` with the same filters as `list_posts`. PDF via fpdf2 with DejaVu from `fonts-dejavu-core` (characters outside the BMP, i.e. emoji, are dropped), falling back to Latin-1 Helvetica.

**Enums** are stored as VARCHAR (`models/base.str_enum`, `native_enum=False`), so adding a platform or post kind needs no migration, just the enum and the frontend's `lib/format.ts` labels.

**Frontend ↔ API is same-origin:** the SPA calls relative `/api`; nginx proxies `/api`, `/docs`, `/redoc`, `/openapi.json` to `backend:8000` via Docker's resolver so nginx boots even when the backend is down. In k8s the ingress routes those paths instead. Mobile-first CSS: a bottom tab bar under 760px, 44px touch targets, 16px inputs. Theme tokens live at the top of `index.css` (red `--primary`, `--brand-gradient` from the logo for buttons/active nav/wordmark, black top bar in both modes). The logo (faceted eye with a speech-bubble iris, Navi's visual language in red/black) and every favicon/app-icon size plus `site.webmanifest` are generated by `scripts/make_icons.py` (Pillow); edit the script and re-run it rather than editing the PNGs. `components/Logo.tsx` renders it in the app.

**Build info:** `APP_VERSION`/`GIT_COMMIT`/`BUILD_TIME` build-args are baked into both images (backend `TABULA_*` env, frontend `VITE_*`), surfaced by `GET /api/build-info` and the About page. Keep these ARGs late in the Dockerfiles.

## Gotchas

- **Tests** run the real app on SQLite with `NullPool` (`DB_NULL_POOL`). They never touch Redis, a browser or a real platform: the `queued` fixture captures `dispatch.enqueue_backfill` (`("backfill@browser", id)` for the browser queue), `fake_platform` replaces `platforms.get_client` with a `FakeClient`, and `tests/test_browser.py` drives browser clients with a `FakeDriver` replaying captured JSON. Worker logic is tested by calling `scan_service` directly.
- **Celery tasks** run each invocation in a fresh `asyncio.run`; use `db.create_worker_engine()` (NullPool, disposed per run), never the shared engine.
- SQLite returns naive datetimes; compare through `tracking_service._aware` or keep comparisons in SQL.
- Relationships use `lazy="raise"` except `Tracking.social_account` (`selectin`): load what you need explicitly in async code.
- After `commit()`, `await db.refresh(obj)` before returning ORM rows: `updated_at` is server-side.
- Don't annotate 204 routes with `-> None`; return `Response(status_code=204)`.
- bcrypt ≥ 5 raises on passwords > 72 bytes; `core/security.py` truncates explicitly.
- SQLAlchemy relationship forward refs stay quoted with `TYPE_CHECKING` imports; ruff `UP037` is intentionally ignored.
- **TypeScript:** import `type FormEvent` / `type ReactNode` from `react`; don't use the `React.*` namespace.
- **Healthchecks:** the worker reuses the backend image, so any compose that runs it must override the HTTP healthcheck (`celery inspect ping`). Frontend checks must hit `127.0.0.1`, not `localhost`.
- Run exactly **one** `beat`. Workers (`-Q default`) and browser workers (`-Q browser`) scale freely; browser throughput is bounded by saved logins × pacing.
- Playwright is only in the `browser` image (`pip install ".[browser]"`); import it lazily (see `driver.py`). Locally, point `TABULA_CHROMIUM_PATH` at a Chromium binary if `playwright install` isn't possible.

## CI, releases & conventions

`.github/workflows/ci.yml`: backend (ruff, pytest on SQLite, `docs/` drift check), backend-postgres (migrations + `alembic check` + pytest on Postgres), frontend (build + vitest), smoke (base compose boots the whole stack; checks health, the nginx proxy, `/openapi.json`, that `/api/trackings` needs auth, that both workers answer a ping, that the browser worker can start Chromium, and that beat runs), chart-lint. **Only on push to `main`:** `version` computes the next semver (seeded at `v1.0.0`) and creates a GitHub release; `api-spec` attaches the versioned OpenAPI spec; `images` builds the backend, browser and frontend images tagged `latest`/`X.Y.Z`/`X.Y`/`sha-<short>`, signs them with cosign and attests provenance; `chart` publishes the Helm chart.

The repo is **squash-merge only**, with the squash commit title = PR title. The PR title must be a Conventional Commit (enforced by `.github/workflows/pr-title.yml`): `feat:` → minor, `fix:`/other types → patch, `!` or a `BREAKING CHANGE` footer → major. See `CONTRIBUTING.md`.

## Deployments

- `docker-compose.yml` (+ dev override) — local/dev; builds images from source.
- `deploy/truenas/` — TrueNAS SCALE compose using the published GHCR images (backend, browser, frontend); only the frontend port (default 30082) is published.
- `charts/tabula/` — Helm chart, published by CI to `oci://ghcr.io/zollo/charts/tabula`. The backend runs `alembic upgrade head` in an init container and scales horizontally; so do the workers (`worker.replicas`, `browserWorker.replicas` or `browserWorker.keda`). `beat` stays at 1 replica.
- Project docs also live in Notion (Joe's preference); keep this file and the README as the in-repo reference.
