-- Denormalized view of the AP control exceptions for BI/API consumption: joins in supplier_id and
-- category from fct_cases so a consumer can filter/group without a second query. Source table is
-- Python-computed (see stg_ap_control_exceptions.sql's header) -- this model only joins and does
-- not change any control's logic.
select
    e.exception_id,
    e.case_id,
    e.control_id,
    e.severity,
    e.exposure_eur,
    e.evidence,
    e.generated_at,
    c.supplier_id,
    c.category
from {{ ref('stg_ap_control_exceptions') }} e
left join {{ ref('fct_cases') }} c on c.case_id = e.case_id
