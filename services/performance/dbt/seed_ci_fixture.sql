-- Tiny fixture for CI's Postgres service container -- same 2-case design as
-- tests/test_sql_analysis_live.py's fixture (repeated here rather than shared, since this one
-- runs via psql against a bare service container with no Python/SQLAlchemy available yet at
-- this point in the CI job): C1 has a repeated "Approve" (rework, 48h cycle time, breaches a
-- 24h target); C2 doesn't (10h cycle time, does not breach).
INSERT INTO staging.events (case_id, activity, timestamp, category) VALUES
    ('C1', 'Create',  '2024-01-01T00:00:00Z', 'Widgets'),
    ('C1', 'Approve', '2024-01-01T10:00:00Z', 'Widgets'),
    ('C1', 'Approve', '2024-01-03T00:00:00Z', 'Widgets'),
    ('C2', 'Create',  '2024-01-06T00:00:00Z', 'Widgets'),
    ('C2', 'Pay',     '2024-01-06T10:00:00Z', 'Widgets');

INSERT INTO analytics.process_cases
    (case_id, first_activity, last_activity, event_count, start_time, end_time,
     cycle_time_hours, supplier_id, category, variant, variant_frequency) VALUES
    ('C1', 'Create', 'Approve', 3, '2024-01-01T00:00:00Z', '2024-01-03T00:00:00Z',
     48.0, 'S1', 'Widgets', 'Create->Approve->Approve', 1),
    ('C2', 'Create', 'Pay', 2, '2024-01-06T00:00:00Z', '2024-01-06T10:00:00Z',
     10.0, 'S1', 'Widgets', 'Create->Pay', 1);

INSERT INTO analytics.suppliers (supplier_id, supplier_name, region, supplier_tier) VALUES
    ('S1', 'Test Supplier', 'EMEA', 'A');

INSERT INTO analytics.sla_rules (category, target_hours) VALUES ('Widgets', 24);
