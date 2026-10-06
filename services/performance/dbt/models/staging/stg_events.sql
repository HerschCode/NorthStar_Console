-- 1:1 with staging.events. No filtering, no renaming (already snake_case) -- staging models
-- exist so every downstream model uses ref() against a staging model instead of a raw table
-- name, which is what lets a source table get renamed/migrated later without touching every
-- intermediate/mart model.
--
-- Real bug, found by running dbt build: an earlier version of this comment used the literal
-- Jinja call as a prose example (a ref() call naming this exact model) -- dbt's Jinja renderer
-- doesn't know SQL "--" comments exist, so it evaluated that text as real Jinja and created a
-- genuine self-referencing dependency edge, failing every build with "Found a cycle:
-- model.operations_performance.stg_events". Fixed by describing the pattern in prose instead
-- of writing a literal ref() call to this same model inside its own file.
select
    case_id,
    activity,
    timestamp,
    resource,
    purchase_order_id,
    item_id,
    category,
    supplier_id,
    item_category,
    gr_based_inv_verif,
    goods_receipt_required,
    document_type,
    item_type,
    company,
    sub_spend_area,
    user_id,
    net_worth_eur
from {{ source('raw', 'events') }}
