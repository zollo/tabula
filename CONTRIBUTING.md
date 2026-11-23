# Contributing to Tabula

## Branching & merging

- Branch off `main` for every change (`feat/…`, `fix/…`, `ci/…`, `docs/…`).
- Open a PR into `main`. CI (lint, tests, smoke, chart lint) must pass.
- PRs are **squash-merged**; the **PR title becomes the commit subject** on
  `main`, and the merged branch is deleted automatically.

## Conventional Commits

The **PR title must follow [Conventional Commits](https://www.conventionalcommits.org/)**,
because it becomes the squash commit that drives automated semantic versioning
on merge to `main`. A CI check (`PR Title`) enforces this.

```
<type>[optional scope][!]: <description>
```

**Types** and their effect on the next release version:

| Type | Example | Version bump |
|------|---------|--------------|
| `feat` | `feat: add Mastodon support` | **minor** (x.**Y**.0) |
| `fix` | `fix: keep the backfill cursor after a rate limit` | **patch** (x.y.**Z**) |
| `feat!` / `fix!` / any `!` | `feat!: change the JSON export format` | **major** (**X**.0.0) |
| `docs`, `chore`, `ci`, `build`, `refactor`, `perf`, `test`, `style`, `revert` | `docs: document Reddit app setup` | patch (default) |

A breaking change can also be signalled with a `BREAKING CHANGE:` footer in the
PR body.

### Guidelines
- Keep the subject imperative and lowercase, no trailing period
  (e.g. `fix: handle deleted accounts`, not `Fixed the bug.`).
- Use a scope when it helps: `feat(worker): …`, `fix(ui): …`.

## API changes

`docs/openapi.json` is the published API contract. After changing a route or
schema, regenerate it and commit it:

```bash
make openapi    # writes docs/openapi.json
```

CI fails if `docs/` is stale.

## Adding a platform

1. Add the value to `models/platform.Platform` (and `PLATFORM_LABELS`).
2. Write a client in `app/platforms/<name>.py` with `resolve(handle)` and
   `fetch_page(profile, cursor)` returning posts **newest first** (see
   `platforms/base.py`), and wire it into `platforms.get_client`.
3. Add any credentials to `settings_service.CREDENTIAL_FIELDS`, `config.py`,
   `.env.example` and the admin Settings page.
4. Test it against canned responses with `httpx.MockTransport`
   (`tests/test_platforms.py`).

## Database changes

Add a migration under `backend/app/migrations/versions/`, chained via
`down_revision`. CI runs `alembic upgrade head && alembic check` against Postgres,
so a model change without a matching migration fails the build.

## Releases

On merge to `main`, CI computes the next `vX.Y.Z` from the PR title, tags it,
creates a GitHub release (with the OpenAPI spec attached), builds/signs/attests the container images (`latest`, `X.Y.Z`, `X.Y`,
`sha-<short>`) and publishes the Helm chart. See the README's **CI / releases**
section.
