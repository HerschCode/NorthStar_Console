# Accounts-payable controls (finance module, Phase F2)

**Framing: internal controls and anomaly triage, not fraud detection.** BPI 2019 (a real, public
procurement event log) has no fraud labels. Nothing here says a flagged case is fraud -- each
control is a rule violation or a statistical anomaly, measured against reality where reality is
available (real exception counts, below) and against synthetic planted anomalies where it isn't
(recall/false-positive rate, `docs/ap-controls-evaluation.md`). The two are never mixed.

Code: `src/controls/ap_controls.py`. Config/thresholds: `config/ap_controls.yaml` (every threshold
there is a stated assumption -- see its header). Run: `python -m scripts.run_ap_controls` (writes
`analytics.ap_control_exceptions`, dbt-exposed as `mart_ap_control_exceptions`). API:
`GET /controls/exceptions`, `GET /controls/summary`.

## The six controls

| ID | What an auditor tests | Logic | Real exceptions (9,228 cases) |
|---|---|---|---|
| C1 | Three-way-match items should have a goods receipt before the invoice clears | Item labelled "3-way match"; case has an invoice-type event but no goods-receipt event at all | 4 |
| C2 | Invoice-vs-goods-receipt order matches what the item's configured match type says | Actual first-invoice-event vs first-goods-receipt-event order compared to the "before GR" / "after GR" label | 3,035 |
| C3 | Purchase orders aren't split into sub-threshold pieces to dodge the $10,000 secondary-approval rule | Same supplier + requester, 2-4 orders each >= 50% of the threshold, within a 7-day rolling window, summing over it | 90 |
| C4 | The same invoice wasn't entered twice | Same vendor, amount within 1%, invoice dates within 5 days, excluding the vendor's own routine recurring amounts | 2,812 |
| C5 | A payment-block override went through a re-approval step | "Remove Payment Block" then "Clear Invoice" with no configured reapproval activity actually functioning as a post-block step in this system (see below); segregation-of-duties sub-flag if the same user created the PO and cleared the invoice | 2,297 |
| C6 | A vendor's invoice amounts aren't suspiciously round | Benford's-law chi-square test on leading digits, vendors with >= 30 invoices | 29 |

## What C1 does NOT check, and why
`net_worth_eur` is a single cumulative running total per case (SAP's own field), not separate
invoiced-amount and received-amount fields. C1 therefore only checks *whether a goods receipt
happened at all*, never whether the invoiced value exceeds the received value -- that comparison
would need a field this dataset doesn't have, and claiming it anyway would be a false precision
claim. Disclosed here rather than silently narrowed.

## C2's real finding, and its caveat
35.8% of labelled 3-way-match cases (3,035 of 8,475) have their invoice and goods-receipt events in
the *opposite* order from what `Item Category` says the item is configured for. That's a real,
substantial pattern in BPI 2019 -- also visible in the wider process-mining literature on this
dataset -- and it is reported as such. It is **not** necessarily a compliance failure: the
"GR-based invoice verification" flag is a system configuration setting, and partial deliveries,
service items and manual corrections can legitimately change the actual sequence without anyone
having done anything wrong. Treat this as "worth a sample review," not as 3,035 confirmed errors.

## C3: two real bugs, fixed before reporting
The first version of C3 flagged 3,288 of 9,228 cases (36%) -- almost all ordinary high-frequency
purchasing from busy suppliers, not splitting. Root cause: "any sub-threshold order" matched a
supplier's whole routine order history, and reporting every row where the rolling sum stayed over
threshold produced one exception per subsequent order, not one per split event. Fixed with
`split_near_threshold_pct` (an order must be a meaningful fraction of the threshold to look like a
deliberate split piece) and `split_max_pos_in_window` (a split is a handful of orders, not dozens),
plus reporting only the first row of each newly-crossed window. Result: 90 exceptions, and the
planted-anomaly recall below is measured on the FIXED version.

## C4: two real bugs, fixed before reporting
The tolerance-check loop had a scoping bug: a single `break` meant to skip past one out-of-tolerance
comparison instead aborted checking *every remaining pair* for a supplier, so almost no real
duplicates were ever reached (the first version reported 112 exceptions, nearly all missed). Fixed
with a proper nested loop. That surfaced a second problem: ~12,000 pairs, almost all a vendor's own
routine recurring amount (a monthly service fee invoiced dozens of times). Fixed by excluding
amounts that recur more than `duplicate_max_recurring_count` times for that vendor across the whole
dataset. Result: 2,812 exceptions -- still substantial, and disclosed as a real limitation below.

## Known false-positive sources (per control)
- **C2**: as above -- order mismatch is a real pattern, not proven non-compliance.
- **C3**: the near-threshold and window-size cutoffs are assumptions; a genuine split using very
  different amounts or a longer time horizon would not be caught, and a supplier who happens to
  place 2-4 similarly-sized orders in a week for unrelated reasons would be.
- **C4**: near-identical amounts from *irregular* billing (e.g. usage-based fees that vary slightly
  each month) can still cluster within tolerance and escape the exact-recurring-amount filter --
  this is the main remaining source of the 2,812 count and needs an invoice/PO reference to fully
  resolve, which this dataset doesn't carry.
- **C5**: this event log has **no distinct override-authorization activity type at all** (see
  below) -- 2,297 "low severity" rows mean "no logged reapproval step," which is close to true by
  construction of this system's process model, not a compliance signal on its own.
- **C6**: Benford deviation is common in legitimate data too (round contract prices, price caps);
  it is a screening signal, never itself evidence.

## C5's honest limitation
Two candidate "reapproval" activities were configured (`SRM: Complete`, `Record Invoice Receipt`).
Both occur in the dataset, but empirically, across all 2,299 cases with a payment-block removal,
they occur *after* the block removal in only 5.4% of cases -- meaning they are not really acting as
a post-override reapproval step in this system's logged process (e.g. `Record Invoice Receipt`
normally happens *before* the block is removed, not after). C5 measures and reports this rate
(`reapproval_activity_post_block_rate` in the evidence) and downgrades severity to "low" rather than
reporting 2,297 "medium" compliance failures on a proxy activity that doesn't function as intended.
0 segregation-of-duties violations were found (no case has the same user creating the PO and
clearing the invoice after an override) -- also a real, reported result.

## Model-risk framing
See `docs/model-risk.md` for how this maps to a model-risk-management checklist (SR 11-7).
