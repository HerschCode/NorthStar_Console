select supplier_id, supplier_name, region, supplier_tier
from {{ ref('stg_suppliers') }}
