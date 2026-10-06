-- 1:1 with analytics.suppliers.
select supplier_id, supplier_name, region, supplier_tier
from {{ source('raw_analytics', 'suppliers') }}
