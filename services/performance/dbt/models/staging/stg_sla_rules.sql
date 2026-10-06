-- 1:1 with analytics.sla_rules.
select category, target_hours
from {{ source('raw_analytics', 'sla_rules') }}
