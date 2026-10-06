# Power BI Build Guide

Manual, click-by-click — Power BI Desktop is a native app, no automation possible.
Every query below is already verified against live data and matches the `/dashboard`
numbers exactly (checked: overall SLA breach rate 93.8% in both).

## 1. Install

Download Power BI Desktop (free): https://www.microsoft.com/en-us/power-platform/products/power-bi/desktop
Run the installer. No license needed for Desktop.

## 2. Install the Postgres driver (one-time prerequisite)

Power BI's PostgreSQL connector needs the Npgsql driver installed separately:
https://www.npgsql.org/ → download the latest `.msi` for your Windows version, install it,
then **restart Power BI Desktop** if it was already open.

## 3. Connect to the database

`Get Data` → search `PostgreSQL database` → `Connect`

| Field | Value |
|---|---|
| Server | `ep-bitter-recipe-aelbhho4-pooler.c-2.us-east-2.aws.neon.tech:5432` |
| Database | `neondb` |

Click **Advanced options**, and instead of picking tables, paste one of the SQL
statements below per visual — this reuses the exact tested queries in
`sql/analysis/*.sql` rather than rebuilding logic from scratch in DAX.

When prompted for credentials: **Username** `neondb_owner`, **Password** — yours to
enter (this is a your-machine-only credential prompt, not something to script).
Set SSL mode to **Require** if asked.

## 4. Build these 5 tiles (matches `/dashboard` + `FEATURES.md` §19)

### Tile 1 — Executive Overview (KPI cards)
```sql
SELECT
    COUNT(*)                                                        AS case_count,
    ROUND(AVG(cycle_time_hours)::numeric, 2)                        AS mean_hours,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cycle_time_hours)::numeric, 2)  AS median_hours,
    ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY cycle_time_hours)::numeric, 2)  AS p90_hours
FROM analytics.process_cases
```
Drag each column onto a separate **Card** visual.

### Tile 2 — Bottlenecks (bar chart)
```sql
WITH stage_durations AS (
    SELECT
        case_id,
        activity || ' -> ' || LEAD(activity) OVER (PARTITION BY case_id ORDER BY timestamp) AS stage,
        EXTRACT(EPOCH FROM (
            LEAD(timestamp) OVER (PARTITION BY case_id ORDER BY timestamp) - timestamp
        )) / 3600.0 AS duration_hours
    FROM staging.events
)
SELECT stage, COUNT(*) AS case_count, ROUND(AVG(duration_hours)::numeric, 3) AS avg_hours
FROM stage_durations
WHERE stage IS NOT NULL
GROUP BY stage
ORDER BY avg_hours DESC
LIMIT 8
```
**Clustered bar chart**: Axis = `stage`, Values = `avg_hours`.

### Tile 3 — SLA breach rate by category (bar or table)
```sql
WITH sla_targets AS (
    SELECT pc.case_id, pc.cycle_time_hours, pc.category,
           COALESCE(sr.target_hours, 240) AS target_hours
    FROM analytics.process_cases pc
    LEFT JOIN analytics.sla_rules sr ON pc.category = sr.category
)
SELECT category, COUNT(*) AS case_count,
       ROUND(100.0 * SUM(CASE WHEN cycle_time_hours > target_hours THEN 1 ELSE 0 END) / COUNT(*), 1) AS breach_rate_pct
FROM sla_targets
WHERE category IS NOT NULL
GROUP BY category
ORDER BY breach_rate_pct DESC
```
**Bar chart**: Axis = `category`, Values = `breach_rate_pct`.

### Tile 4 — Supplier scorecard (table)
```sql
WITH sla_eval AS (
    SELECT pc.*, COALESCE(sr.target_hours, 240) AS target_hours
    FROM analytics.process_cases pc
    LEFT JOIN analytics.sla_rules sr ON pc.category = sr.category
)
SELECT supplier_id, COUNT(*) AS order_count,
       ROUND(AVG(cycle_time_hours)::numeric, 2) AS avg_cycle_time_hours,
       ROUND(AVG(CASE WHEN cycle_time_hours > target_hours THEN 1.0 ELSE 0.0 END)::numeric, 3) AS sla_breach_rate
FROM sla_eval
GROUP BY supplier_id
HAVING COUNT(*) >= 5
ORDER BY avg_cycle_time_hours
LIMIT 10
```
**Table visual**, all 4 columns.

### Tile 5 — Rework signal (table)
```sql
SELECT case_id, activity, COUNT(*) AS occurrence_count
FROM staging.events
GROUP BY case_id, activity
HAVING COUNT(*) > 1
ORDER BY occurrence_count DESC
LIMIT 50
```
**Table visual**. Note: this is a partial signal (repeated activities), not the full
Python conformance check — see `sql/analysis/conformance.sql`'s own comment for why
the complete conformance logic stays in Python (`GET /metrics/conformance`).

## 5. Publish

`File` → `Publish` → `Publish to Power BI` (needs a free Power BI service account,
separate sign-in from Desktop) → this gives you a shareable link for your README,
same as the Looker Studio option would have.

## 6. Screenshot

Take a screenshot of the finished report, save to `dashboard/screenshots/`, and link
it from `dashboard/README.md` and the main `README.md`.
