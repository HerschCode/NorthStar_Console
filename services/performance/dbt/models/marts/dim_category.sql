-- Named dim_category, not dim_business_unit -- this project's data has no business_unit field
-- (docs/data-contract.md). category is the real segmentation column this project uses
-- throughout (see sql/analysis/rolling_sla_breach_rate.sql's header for the same note). Named
-- honestly here rather than aliased to imply a column that doesn't exist.
select
    category,
    target_hours as sla_target_hours
from {{ ref('stg_sla_rules') }}
