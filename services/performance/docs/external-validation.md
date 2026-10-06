# External validation of the AP controls (public, non-BPI data)

Reproduce: download the two files below into `data/external/` (git-ignored), then
`PYTHONPATH=. python -m scripts.external_validation` -> `reports/external_validation.json`.
Controls and `config/ap_controls.yaml` are used **unchanged** -- nothing was tuned on these data.

- UCI Online Retail II: <https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip> (45 MB)
- SEC EDGAR Financial Statement Data Sets 2024q1: `https://www.sec.gov/files/dera/data/financial-statement-data-sets/2024q1.zip` (124 MB;
  SEC rejects requests without a User-Agent naming a contact -- use your own).

## Online Retail II (36,969 sale invoices, 5,878 customers, 7,901 credit notes)
Mapping: invoice -> case; customer -> supplier/user; invoice total (GBP, no conversion) -> order value. This is a
*sales* ledger standing in for payables, so only the schema-level logic transfers.

| Control | Result |
|---|---|
| C4 duplicate invoice | 343 invoices flagged (0.93%). 21.9% were later reversed by a credit note from the same customer for ~the same amount, vs a 1.0% base rate: **21x lift**. Caveat: a reversal is not a duplicate label (returns happen for many reasons), so this shows the control isolates unusual same-amount repeats, not that it finds duplicates. |
| C3 threshold splitting | 15 flags; only 75 invoices exceed the 10,000 threshold at all (GBP vs EUR threshold applied unchanged). |
| C6 Benford | **Flags 56% of eligible customers (84 of 151) against a 5% null.** Pooled invoice totals deviate too (MAD 0.020; Nigrini "close conformity" is < 0.006): price points, not fraud. |

## SEC 2024q1 (2.1M USD values, 5,938 filers) -- base-rate check for C6
- Pooled across all filers, Benford holds closely (MAD 0.0023, under the 0.006 "close conformity" line).
- Per filing (>= 30 values, 5,841 filings), the control's rule **flags 55%** (null 5%); 51% even when also requiring MAD > 0.015.
  The flag rate rises with sample size (72% at 30-100 values, 91% above 2,000): a chi-square test on
  structured financial-statement values is rejecting tiny, systematic deviations, not finding anomalies.

## Conclusion (a negative result for C6, a modest positive for C4)
- **C6 as configured is not a usable screen**: it fires on most ordinary real data in both sets. Using it
  would bury reviewers. If kept, it needs an effect-size gate (e.g. MAD) and a much lower alert rate; I have
  not implemented or tuned that here -- this document is the evidence for doing it deliberately.
- C4 behaves sensibly on out-of-domain data with a real (if indirect) outcome signal.
- Limits: sales-not-AP data; no ground-truth duplicates or fraud labels in either set.
