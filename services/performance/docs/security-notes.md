# Security Notes

Small, deliberately non-exhaustive -- this is a portfolio project, not a production system with a
real threat model. What's here is meant to demonstrate the *practice*, not claim the system is
hardened for real deployment.

## Credentials
- Never committed -- `.env` is gitignored, `.env.example` documents required variables with no
  real values.
- Three separate DB roles instead of one shared credential (`sql/schema/004_create_roles.sql`,
  applied manually by an admin -- see the comment in `scripts/setup_database.py` for why it's not
  automatic):
  - `ops_pipeline_writer` -- used by `scripts/run_pipeline.py` and `scripts/setup_database.py`
    (via `DB_USER`/`DB_PASSWORD`). The only role with write/DDL access.
  - `ops_api_reader` -- used by `src/api/` via a dedicated `API_DB_USER`/`API_DB_PASSWORD`
    env pair (`src/api/db.py:get_engine`), separate from the pipeline's credential, so the
    API can never write to the database even if a bug in the code tried to. Falls back to
    `DB_USER`/`DB_PASSWORD` if the API-specific vars aren't set, for simple local dev before
    roles are provisioned. SELECT-only, including `pipeline_runs` (needed for
    `GET /observability/pipeline-runs`).
  - `ops_readonly_report` -- narrower still, for a BI tool: SELECT on `analytics` only, explicitly
    **not** on `pipeline_runs` -- a business dashboard has no reason to see pipeline operational
    metadata.
- **Rotation**: no automated rotation exists (that's genuinely out of scope for this project's
  size). If a credential is ever exposed, the practical rotation steps are: `ALTER ROLE <role> WITH
  PASSWORD '<new>'` in Postgres, update `.env` (and whatever secrets store a real deployment would
  use -- GCP Secret Manager is the natural fit given the BigQuery migration), then restart the
  API/pipeline processes that hold the old credential in memory.

## API surface
- `src/api/` is entirely `GET` -- no endpoint mutates data. This is enforced by only registering
  `allow_methods=["GET"]` in the CORS config (`src/api/main.py`) and by every route in
  `src/api/routes.py` being a read.
- CORS previously defaulted to `allow_origins=["*"]`; fixed to read from an `API_ALLOWED_ORIGINS`
  env var (comma-separated), defaulting to `localhost` only rather than open-to-everyone if unset.
  An analytics API exposing operational business data shouldn't be reachable from an arbitrary
  origin by default, even during early development.
- **API-key authentication is now real** (`src/api/auth.py`, built in a later session) -- every
  endpoint except `GET /health` requires a matching `X-API-Key` header, checked with
  `secrets.compare_digest` (constant-time, avoiding a timing side-channel a naive `==` comparison
  would have). `/health` is deliberately exempt -- health checks are conventionally
  unauthenticated so a load balancer/orchestrator can probe liveness without a credential, a
  standard practice, not an oversight. If neither `API_KEY` nor `API_KEYS` is set, auth fails
  **open** for local development, matching this project's other permissive local-dev defaults --
  a real deployment must set one.
- **Per-client keys with named roles now exist** (`API_KEYS`, comma-separated `name:key:role`
  triples) -- revoking one caller's access no longer requires rotating everyone else's key, which
  the original single-shared-secret design did. `API_KEY` still works too (mapped to a synthetic
  "default" admin client), so an already-configured deployment doesn't break. `require_role()`
  exists for gating a specific route to a specific role; every route in this project is currently
  read-only so no route actually uses it yet -- it's real, tested infrastructure ready for the
  first route that needs it, not speculative scaffolding.

## SQL injection
- All queries go through SQLAlchemy's parameterized queries (`text(...)` with bound params, e.g.
  `GET /observability/pipeline-runs`'s `%(limit)s`) or pandas' `read_sql` with a `params=` dict --
  never raw string interpolation into SQL.
- The agent-facing tools in `operations-assistant` (a separate project) are specifically designed
  around this same principle: the LLM never writes or sees raw SQL, only calls fixed, parameterized
  functions -- see that project's `docs/tool-design.md` once built.

## What's explicitly NOT done here, on purpose
- No rate limiting on the API (Tier 3)
- No automated key rotation (revoking/issuing a key is still a manual env var edit + redeploy)
- No encryption-at-rest configuration for Postgres (assumed handled by the hosting environment,
  not this codebase's concern)
- No dependency vulnerability scanning in CI yet -- a reasonable `pip-audit` addition to
  `.github/workflows/test.yml` if this project keeps growing

Being explicit about what's out of scope is itself the point -- a security section that claims
completeness without saying what it doesn't cover is a worse signal than an honest, bounded list.
