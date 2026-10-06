# Operations Performance — Feature Specification

**Client scenario:** Northstar Manufacturing wants to understand why procurement (Procure-to-Pay)
orders are taking too long, where the process breaks down, which suppliers/business units are
responsible, which orders are at risk of missing SLA, and what to do about it.

**Data foundation:** BPI Challenge 2019 (real, public procurement event log) as the process
backbone, augmented with synthetic business context (suppliers, business units, SLA rules) that
the real log doesn't include. `docs/data-contract.md` documents exactly what is real vs. synthetic.

Tiering: **Tier 1 = required to call this project done. Tier 2 = what makes it a strong,
above-fresher project. Tier 3 = advanced, build only if time remains — never at the cost of Tier 1/2.**

---

## 1. Data acquisition & documentation
- [T1] Download and inspect the BPI Challenge 2019 event log; identify case ID, activity, timestamp, resource, org fields
- [T1] Data dictionary (`docs/data-dictionary.md`) — every field: description, type, example, source, business meaning
- [T1] Data provenance note — which fields are real (BPI) vs. synthetic (business context)
- [T2] EDA report — distributions, missing-data patterns, categorical breakdowns, timestamp ranges, outliers

## 2. Data ingestion (ETL)
- [T1] Repeatable ingestion pipeline: raw → validate → parse → clean → transform → load
- [T1] Schema validation, datatype conversion, timestamp parsing
- [T1] Duplicate detection, missing-value handling, invalid-record handling
- [T2] Ingestion logging + a failed-record report (not silent drops)

## 3. Data quality
- [T1] Data Quality Report — missing values, duplicate records/cases, invalid or impossible
      timestamps, inconsistent IDs, incomplete cases, unusual activity sequences, outliers
- [T1] Document every cleaning decision and its justification

## 4. Data modeling (PostgreSQL)
- [T1] Relational schema: `purchase_orders`, `purchase_order_items`, `suppliers`, `events`,
      `process_cases`, `process_metrics`, `sla_predictions`
- [T1] Primary/foreign keys, constraints, indexes
- [T2] Views for the most common analytical queries
- [T3] Query-plan review / basic performance tuning

## 5. SQL analytics
- [T1] Core SQL: SELECT/WHERE/GROUP BY/HAVING/CASE/JOIN/UNION/CTEs/subqueries
- [T1] Window functions: `LAG`, `LEAD`, `ROW_NUMBER`, `RANK`, `DENSE_RANK`, `SUM()/AVG() OVER()`
- [T1] Business queries: slowest suppliers, worst departments, SLA breach rate by category,
      month-over-month cycle-time trend
- [T2] Cohort analysis, period-over-period comparison, rolling metrics

## 6. Process performance analytics
- [T1] Cycle time (total + per-stage), waiting time vs. processing time
- [T1] Throughput over time
- [T1] SLA compliance / breach rate, overall and segmented
- [T2] Median vs. mean, percentile, variance — not just averages

## 7. Bottleneck analysis
- [T1] Identify slowest stages, highest-waiting stages; quantify % contribution to total delay

## 8. Process variant analysis
- [T2] Identify distinct paths cases actually take through the process; frequency, avg duration,
      SLA breach rate, and business-unit distribution per variant

## 9. Process conformance
- [T2] Define the expected P2P sequence (`config/process.yaml`); detect skipped steps, repeated
      activities, backward transitions, missing approvals
- [T2] Conformance Report: % compliant, top deviations, delay cost of each deviation type

## 10. Rework analysis
- [T2] Rework rate, most-repeated activity, which business units/suppliers generate the most
      rework, rework's effect on cycle time

## 11. Root-cause / driver analysis
- [T2] Cycle time vs. supplier / order value / business unit / variant / rework / approval level
- [T2] State findings as association, not causation, unless causality is actually established

## 12. Supplier performance
- [T2] Supplier scorecard: volume, avg cycle time, delivery delay, SLA breach rate, rework rate
- [T2] Minimum-volume threshold so low-volume suppliers don't skew rankings

## 13. Business-unit / segment analysis
- [T2] Compare departments, regions, categories on cycle time, SLA compliance, volume, rework

## 14. Trend analysis
- [T2] Daily/weekly/monthly/quarterly trends; flag deteriorating suppliers or worsening stages

## 15. Statistical analysis
- [T2] Correlation, group comparisons, distribution checks — always tied to a specific business
      question, never applied indiscriminately
- [T3] Significance testing / confidence intervals where genuinely justified

## 16. Predictive SLA-risk model
- [T1] Binary classification: will this case breach SLA? Baseline (logistic regression) +
      stronger model (random forest / gradient boosting)
- [T1] Features: order value, supplier history, stage, elapsed time, rework count, business unit
- [T1] Evaluation: precision, recall, F1, ROC-AUC, confusion matrix; explicit precision/recall
      trade-off discussion (cost of a missed risky order vs. a false alarm)
- [T2] Explainability — per-prediction top contributing factors (not just a probability)
- [T3] SHAP, model comparison, monitoring/drift discussion

## 17. Scenario / what-if analysis
- [T3] Estimate historical impact of hypothetical changes (e.g. "if approval time drops 20%") —
      clearly labeled as scenario estimates, not guarantees

## 18. Business reporting
- [T1] Every material finding follows: Finding → Evidence → Impact → Recommendation
- [T2] Automated management report generator (weekly/monthly summary)

## 19. Dashboard (Power BI / Looker Studio)
- [T1] Executive Overview (KPIs), Process Performance, Bottlenecks, SLA/Risk
- [T2] Supplier Performance, Conformance, Recommendations pages
- [T3] Looker Studio version on top of BigQuery (see §20)

## 20. Cloud migration (GCP)
- [T2] Load cleaned data into BigQuery; port key SQL analysis to BigQuery SQL
- [T2] Understand and apply partitioning/clustering; be able to explain query-cost implications
- [T2] Looker Studio dashboard reading from BigQuery
- [T3] Cloud Storage as the landing zone for raw files, IAM basics, Secret Manager for credentials

## 21. Engineering hygiene
- [T1] `src/` organized by pipeline stage (ingestion → cleaning → transformation → analytics → ml → reports), not generic "utils/services/managers"
- [T1] Tests for cleaning, transformation, analytics, and ML (happy path + at least one edge case each)
- [T2] Config-driven business rules (`config/sla.yaml`, `config/process.yaml`) instead of hardcoded constants
- [T3] CI (GitHub Actions) running tests on push

---

## Definition of done
**MVP (interview-ready minimum):** sections 1–8, 16, 18, 19 (local Power BI version), all Tier 1.
**Strong version (what you should actually ship):** MVP + all Tier 2.
**Stretch:** Tier 3, only after Project 2's Tier 1 is also done — don't let this project's polish
delay starting Project 2.
