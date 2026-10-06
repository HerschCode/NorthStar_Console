# Roadmap 2026–27 (feature freeze: integrate, deploy, prove, then deepen)

**Cost policy: free only.** No Vertex AI (the opt-in `GEMINI_BACKEND=vertex` path stays off), no Cloud SQL, no load balancer/NAT.
Models: Gemini via Google AI Studio free key, Groq free key, and local open models (Ollama, ONNX). Data: Neon free tier.
Cloud: Cloud Run scale-to-zero inside the free quota, with the budget guard in `infra`.

Done (Phase 1, mostly): monorepo import with history; ID-token auth and manual-deploy branches merged; 15 workflows hoisted to the root;
root `docker-compose.yml` and `Makefile`; tests re-pointed (gateway 1049 pass, console 26 pass). Left in Phase 1: run the workflows on GitHub, run the
performance/assistant suites, archive the old repos.

**Pace:** the core that matters for applications is Phases 1-4 and takes about 8-10 weeks of focused work (to mid-December), not June.
Phases 5-7 are depth that can be added while you apply; Phase 8 (packaging) should start as soon as Phase 3 is live.

| Phase | When | Goal | Role signal |
|---|---|---|---|
| 1 Consolidate | Oct 2026 | One repo that builds, tests and runs with one command | MLOps, platform |
| 2 Live data + free models | Oct–Nov | Real Neon DB, keys, whole stack running locally | Data eng, DS |
| 3 Deploy on free tier | Nov–Dec | Public URL, applied Terraform, no paid services | MLOps, cloud |
| 4 MLOps loop | Dec | Registry, drift → retrain → gated promote, all automated | MLOps (top differentiator) |
| 5 Data engineering depth | Jan–Feb | Contracts, incremental loads, one streaming path, lakehouse files | Data eng |
| 6 Evaluation rigor | Feb–Mar | Fix weak numbers honestly, independent labels, causal/uplift on real-ish data | Data science |
| 7 Security depth | Mar–Apr | ML supply chain, OWASP/ATLAS mapping in CI, independent test | Cybersec for AI |
| 8 Packaging | from Dec, ongoing | Demo, video, write-ups, resume bullets | All |

## Phase 1: Consolidate
1. Merge `feat/google-id-token-auth` (gateway, assistant): service-to-service identity needed for private Cloud Run. Also `ci/manual-deploy-workflow`.
2. Hoist the 11 nested workflows to root `.github/workflows/` with `paths:` filters and `working-directory`; keep the weekly security cron.
3. Root `docker-compose.yml` + `Makefile` (replace Windows-only `dev.ps1`), `make up|test|lint`.
4. Point `sync-specs` remote mode at this repo; archive the four old repos last (hard to undo, ask first).
5. Rotate any pasted credentials (see Phase 2).
*Relevance:* a reproducible single repo with path-filtered CI is the baseline every MLOps/platform interviewer checks.

## Phase 2: Live data and free models
1. Load Neon (new project) from your own machine, with the password set only in environment variables:
   `cp services/performance/.env.example services/performance/.env` (ignored by git), fill `DB_*`, `DB_SSLMODE=require`, then
   `python -m scripts.setup_database` → `python -m scripts.run_pipeline` → `python -m src.ml.train` → `cd dbt && dbt build --profiles-dir .`
   Free tier is small: load the sample, check `pg_database_size` afterwards, and drop raw staging you do not serve.
2. Keys in the assistant's environment only: `GEMINI_API_KEY` (AI Studio), `GROQ_API_KEY`; run `python -m scripts.check_free_models --probe`.
3. Local open model fallback: Ollama (`-Ollama` flag) so demos never depend on a quota.
4. Run the live e2e story (`npm run e2e:live`) against the real stack.
*Relevance:* proves the pipeline works on a real database, not a snapshot.

## Phase 3: Deploy on the free tier
1. Create the GCP project **only now** (trial credits start counting at activation; free trial is for the project's services, not Vertex).
2. Apply `infra/envs/bootstrap` by hand, set repo variables, `plan` → `apply` for `envs/dev` through CI.
3. Replace SQLite state (spend counters, ledger, conversations) with Neon tables behind the existing interfaces (not Cloud SQL).
4. Deploy P1/P2/P3 + console; only the gateway is public; keep uptime checks that expect 403 on private services.
5. Cost evidence: screenshot of the budget and billing report after a week.
*Relevance:* "deployed, private by default, under a budget" is rare in portfolios and maps directly to cloud/MLOps and security roles.

## Phase 4: MLOps loop (the main differentiator)
1. MLflow with a **model registry** (stages, aliases), served model pulled by alias; keep the SQLite/Neon backend or free DagsHub/MLflow hosting.
2. Drift (PSI exists) → Evidently or the existing report → retrain workflow → challenger vs champion gate → promote → rollback.
3. CI that fails a model PR on a metric regression or a calibration regression (the earlier calibration bug becomes a test).
4. Data versioning (DVC with free remote, or content hashes in the registry) and a model card per release.
5. Shadow/canary on Cloud Run with revision traffic splitting.
*Relevance:* MLOps/ML-platform roles rank this above any single model score. Your honest-bug write-ups are already evidence of good practice.

## Phase 5: Data engineering depth
1. dbt contracts + Great Expectations/Soda checks as a pipeline gate; incremental models and snapshots.
2. One streaming path replay: event log → Redpanda/Kafka-compatible (free local) or Pub/Sub free quota → staging, with idempotent upserts.
3. Parquet/Iceberg (DuckDB reads) as a cheap lakehouse layer; cost/size comparison versus Postgres.
4. Orchestration: keep Prefect; add one Airflow or Dagster DAG so the resume names the tools postings ask for.
*Relevance:* data-engineering roles screen for contracts, idempotency, incremental loads and orchestration.

## Phase 6: Evaluation rigor (data science)
1. Re-run prefix/GRU/BPI 2012 results with PO-grouped temporal validation; replace the degenerate 96% breach target in headline numbers.
2. Calibration + conformal intervals; decision-quality metric (queue precision at k, expected loss avoided) beside ROC-AUC.
3. Uplift: keep labelled as simulated; add one randomized-holdout design doc and power analysis.
4. RAG: fix polarity flips (NLI evidence-first or small local judge), independent labels (second annotator or public sets), report CIs.
*Relevance:* data-science hiring screens for validity and honesty; you already have the habit, this makes it systematic.

## Phase 7: Security depth (the add-on)
1. Model/artifact supply chain: sign images and models (cosign/Sigstore), verify at deploy, scan `joblib`/pickle loads, SBOM-gated release.
2. Run garak and AgentDojo in CI on a schedule; track the 3 open red-team findings to closure.
3. OWASP LLM Top 10 + MITRE ATLAS mapping kept as a generated doc; STRIDE threat model for the whole system.
4. Retest with someone independent (a friend, a CTF/bug-bounty peer) and publish the result either way.
*Relevance:* AI security is a thin talent pool; ML + security in one project is the standout combination.

## Phase 8: Packaging
README top section with live link, 3-minute video, one architecture diagram, "mistakes and fixes" page, resume bullets with measured numbers,
and a short blog post per phase (4, 5, 7 are the strongest).

## Credential hygiene
Neon, Gemini and Groq credentials go in `.env` (git-ignored), repo secrets or Secret Manager only. A password pasted into chat should be rotated.
