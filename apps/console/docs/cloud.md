# Cloud plan for the console (nothing here has been applied)

Decisions already made in code:
- **One public API.** The console reads analytics through the gateway's `GET /v1/data/v1/...` (allow-listed, read-only,
  trace-propagating) and does everything AI/action through the rest of `/v1`. P1 and P2 stay private (ID-token invoker = gateway SA /
  assistant SA, as in `northstar-infra`).
- **No database needed to demo**: P1 serves its committed per-endpoint snapshots; P2's ledger, traces, spend counters and
  investigations are SQLite today.

To add in `northstar-infra` (needs `scripts/install-tools.sh` to verify with its policy suite, which this session could not run):
1. A `cloud_run_service` for `northstar-console` (public ingress, its own service account with no other roles), CORS origin
   `GATEWAY_CORS_ORIGINS` set on the gateway to the console URL.
2. Gateway environment: `P1_URL`, `P2_URL`, `P2_API_KEY` (Secret Manager, one reader), `GATEWAY_DEMO_MODE=1` only for the portfolio demo.
3. Persistence behind the existing interfaces: spend counters (P2 `SpendGuard`) and the intervention ledger in Firestore or Cloud SQL
   (the SQLite files are ephemeral on Cloud Run); a log sink from gateway stdout to a BigQuery dataset for governance history.
4. Policy additions with mutation tests: console service has no IAM bindings; only the gateway is `allUsers`-invokable among the API services.
5. Canary: Cloud Run traffic splitting variables per service for the incident drill.
