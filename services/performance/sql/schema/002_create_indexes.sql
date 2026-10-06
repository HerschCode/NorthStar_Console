-- Composite, not two single-column indexes: every window function in sql/analysis/ partitions
-- by case_id and orders by timestamp together (PARTITION BY case_id ORDER BY timestamp) -- a
-- composite (case_id, timestamp) index serves that access pattern directly, where two separate
-- single-column indexes would each only narrow one predicate and still require a sort.
-- Found live (not assumed): this file existed with single-column indexes from early in the
-- project, but nothing had ever actually run it against the deployed Neon instance -- pg_indexes
-- showed zero indexes on the staging schema. See docs/sql-window-functions.md for the
-- EXPLAIN ANALYZE before/after (the plan changes from Seq Scan to Index Scan; at this table's
-- current 32K-row size the measured execution time does not, since it was never I/O-bound).
CREATE INDEX IF NOT EXISTS idx_events_case_id_timestamp ON staging.events(case_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_process_cases_supplier ON analytics.process_cases(supplier_id);
CREATE INDEX IF NOT EXISTS idx_process_cases_category ON analytics.process_cases(category);
