-- One row per case -- the grain everything else in this mart layer joins against.
select
    s.case_id,
    s.purchase_order_id,
    s.category,
    s.supplier_id,
    s.start_time,
    s.end_time,
    s.cycle_time_hours,
    s.sla_target_hours,
    s.sla_breach,
    coalesce(rw.rework_event_count, 0) as rework_event_count,
    coalesce(rw.has_rework, false) as has_rework,
    pc.event_count,
    pc.variant,
    pc.variant_frequency,
    pc.first_activity,
    pc.last_activity
from {{ ref('int_case_sla_scored') }} s
left join {{ ref('int_case_rework') }} rw on rw.case_id = s.case_id
left join {{ ref('stg_process_cases') }} pc on pc.case_id = s.case_id
