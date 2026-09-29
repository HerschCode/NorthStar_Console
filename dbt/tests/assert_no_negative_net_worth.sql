-- Custom singular test: a cumulative order value should never be negative. net_worth_eur is real
-- BPI 2019 data (added 2026-09-28, finance module), NULL where the source event doesn't report a
-- value; only non-null rows are checked.
select *
from {{ ref('stg_events') }}
where net_worth_eur is not null and net_worth_eur < 0
