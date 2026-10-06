# API latency: measured, including why the absolute number isn't trustworthy yet

No latency benchmark existed anywhere in this repo before this — an external
review flagged that gap directly (`docs/production-readiness-review.md`
already separately noted "not yet proven for API failures under real load").
Measured it with `scripts/measure_api_latency.py` against a locally-run
server pointed at the real, live Neon Postgres instance (not a mock, not a
local DB) — same code, same data, real network round-trip.

## What was measured (server-side `duration_ms`, from this project's own structured logs)

| Endpoint | Samples | Min | Median | Max |
|---|---|---|---|---|
| `GET /health` | 5 | 281ms | 313ms | 4,828ms |
| `GET /metrics/sla` | 22 | 4,282ms | 5,953ms | 79,610ms |
| `GET /metrics/bottlenecks` | 4 | 8,703ms | 22,438ms | 27,734ms |
| `GET /suppliers/performance` | 1 | 3,953ms | — | — |
| `GET /metrics/conformance` | 1 | 11,000ms | — | — |
| `GET /orders/{case_id}/risk` | 1 | 4,141ms | — | — |

Every endpoint costs multiple seconds. That's real — but the honest next
question is *why*, and the answer has two separate parts, not one.

## Root cause, part 1 (confirmed in code, real regardless of network): no caching

`src/api/db.py`'s `load_cases()` and `load_events()` run
`pd.read_sql("SELECT * FROM analytics.process_cases", engine)` /
`"SELECT * FROM staging.events"` — the **entire table, every column** — fresh,
on **every single request**, then every downstream analytics function
(`evaluate_sla`, `sla_summary`, bottleneck detection, etc.) recomputes from
scratch. No `@lru_cache`, no memoization, no materialized view, no
request-scoped reuse across endpoints that need the same underlying data —
checked directly, none exists anywhere in `src/api/`. This part of the cost
is real no matter how fast the network is, and it gets strictly worse as the
underlying tables grow.

## Root cause, part 2 (this measurement environment, NOT confirmed as a production issue): network path to Neon

Even `GET /health` — which does nothing but a trivial connectivity check —
spiked to 4.8 seconds once in this sample. That's not explainable by "no
caching," since there's nothing to cache on a health check. Compared this
machine's numbers against Render's own live health check
(`https://operations-performance.onrender.com/health`, unauthenticated,
same DB, same network path Render actually uses in production):

```
attempt 1: 4.40s   (cold)
attempt 2: 0.69s
attempt 3: 0.61s
```

**Render-to-Neon, once warm, is sub-second.** This machine's path to Neon —
wherever this development session happens to be running — is measurably
slower and noisier than Render's own path, by a wide margin. That means the
multi-second numbers in the table above are **not a trustworthy measurement
of production latency** — they're dominated by this specific benchmarking
environment's network conditions to the same database, not by the deployed
service's real-world cost.

## The honest conclusion

- **Real, code-confirmed finding, independent of network:** there is no
  caching layer anywhere in this API. Every request re-reads and
  re-computes everything from the full underlying tables. Under real
  concurrent load, or as the dataset grows past 3,000 cases, this will cost
  more DB round-trips and more CPU per request than a cached or
  precomputed-view approach would — a real scalability ceiling, found and
  stated plainly rather than assumed away by a possibly-flattering local
  number.
- **Not yet measured honestly: what this actually costs on Render itself.**
  This benchmark needs to be re-run *from* the deployed environment (or a
  machine with comparable network locality to Neon), not from this
  development machine, before quoting an absolute p50/p95/p99 that means
  anything for a portfolio claim. Filed here as the explicit next step,
  not silently skipped or replaced with a number that would have looked
  better but wasn't actually representative.

## Reproducing this

```bash
uvicorn src.api.main:app --port 8000 &
python scripts/measure_api_latency.py --url http://localhost:8000 \
    --api-key <your API_KEY> --case-id <a real case_id from analytics.process_cases>
```

Real per-request numbers are also always available after the fact from the
structured request logs (`{"logger": "api.requests", "duration_ms": ...}`)
without needing to run a separate benchmark script at all.
