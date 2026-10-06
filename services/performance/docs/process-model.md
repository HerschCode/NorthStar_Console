# Process Model

## Expected Procure-to-Pay sequence
Defined in `config/process.yaml`, enforced by `src/analytics/conformance.py`:

```
Purchase Requisition
      |
      v
   Approval
      |
      v
Purchase Order
      |
      v
Goods Receipt
      |
      v
Invoice Receipt
      |
      v
   Payment
```

## What counts as a deviation
See `docs/analytical-methodology.md`'s Conformance section for the precise definitions. Summary:
a case is non-conformant if it skips an expected step, contains an activity outside this sequence,
repeats an activity not in `allowed_repeats`, or has expected steps occurring out of order.

## Allowed repeats
`config/process.yaml`'s `allowed_repeats` list currently contains `Goods Receipt` -- a purchase
order with multiple line items legitimately produces multiple Goods Receipt events, and that's
normal operation, not rework. Any other repeated activity (e.g. a second Approval) is treated as
rework by `src/analytics/rework.py` and as a conformance deviation.

## Known gap
The current expected sequence is a single linear path. Real P2P processes sometimes have
legitimate parallel or conditional branches (e.g. a 2-way match vs. 3-way match approval path
depending on order value or category) that this model doesn't yet represent -- those would
currently show up as conformance deviations even when they're valid alternate paths. A category-
or value-dependent expected sequence (multiple entries in `process.yaml`, selected by case
attributes) is the natural next step if that turns out to matter once run against real data.
