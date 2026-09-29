-- Finance module Phase F1: real BPI 2019 fields the original 8-column sample dropped, needed for
-- AP controls (docs/ap-controls.md) and working-capital metrics. See docs/data-contract.md.
ALTER TABLE staging.events ADD COLUMN IF NOT EXISTS item_category TEXT;
ALTER TABLE staging.events ADD COLUMN IF NOT EXISTS gr_based_inv_verif BOOLEAN;
ALTER TABLE staging.events ADD COLUMN IF NOT EXISTS goods_receipt_required BOOLEAN;
ALTER TABLE staging.events ADD COLUMN IF NOT EXISTS document_type TEXT;
ALTER TABLE staging.events ADD COLUMN IF NOT EXISTS item_type TEXT;
ALTER TABLE staging.events ADD COLUMN IF NOT EXISTS company TEXT;
ALTER TABLE staging.events ADD COLUMN IF NOT EXISTS sub_spend_area TEXT;
ALTER TABLE staging.events ADD COLUMN IF NOT EXISTS user_id TEXT;
ALTER TABLE staging.events ADD COLUMN IF NOT EXISTS net_worth_eur NUMERIC;
