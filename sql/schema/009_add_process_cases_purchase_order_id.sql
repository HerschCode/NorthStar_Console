-- Leak fix (2026-10-03): supplier-history features use only supplier cases completed before the scored
-- case starts; the dbt models carry purchase_order_id through for that. The pipeline already writes it
-- (build_process_cases.py rebuilds the table with pandas), but a database created from these files
-- (CI, a fresh setup) lacked the column and dbt build failed.
ALTER TABLE analytics.process_cases ADD COLUMN IF NOT EXISTS purchase_order_id TEXT;
