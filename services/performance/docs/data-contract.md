# Data Contract — Real vs. Synthetic

Full field-level breakdown lives in `docs/data-dictionary.md`. This doc is the honesty statement:
what in this project is real data vs. generated, and why, so nobody mistakes one for the other.

## Real
The event log itself -- case IDs, activities, timestamps, resources, and the PO/item/category/
vendor fields -- comes from the **BPI Challenge 2019** procurement process-mining dataset, a public
dataset from an actual company's procurement system. This is what makes the process-analysis
findings (bottlenecks, variants, conformance, rework) meaningful: they're patterns in real
operational data, not patterns designed into synthetic data to look interesting.

**2026-09-28 (finance module, Phase F1):** the sample originally kept only 8 columns and silently
dropped several other real fields the source XES carries -- match type (`Item Category`), goods-
receipt/invoice-verification flags, document/item type, company, sub-spend-area, the per-event
actor (`User`, distinct from `org:resource`), and cumulative order value (`Cumulative net worth
(EUR)`). Verified present by streaming and inspecting the first traces' attribute keys before
adding them (not assumed from documentation) -- see `scripts/download_bpi2019_sample.py`. These are
REAL BPI 2019 fields, not synthetic additions; they were just never wired through. Used by the new
AP-controls module (`docs/ap-controls.md`) and the working-capital metrics.

## Synthetic
- **SLA targets** (`config/sla.yaml`) -- BPI 2019 doesn't include contractual SLA thresholds, so
  these are invented business rules, chosen to be plausible for a procurement process but not
  derived from any real contract.
- **Approval threshold used by the splitting control** (`$10,000`, `docs/ap-controls.md` C3) --
  taken from `operations-assistant`'s synthetic procurement-policy document (Section 4.2), applied
  to the real EUR order-value field as a stated assumption (the source data's currency is EUR, not
  the policy document's implied USD; no conversion is applied -- this is disclosed, not corrected).
- **Anomaly rates in the planted-anomaly evaluation** (`docs/ap-controls-evaluation.md`) --
  synthetic injections at chosen rates, used only to measure recall/false-positive rate; never
  mixed with or presented as real exception counts.
- **Expected process sequence** (`config/process.yaml`) -- the "correct" P2P path used for
  conformance checking is a reasonable generic P2P sequence, not Northstar's (fictional) actual
  documented process.
- **Policy/SOP documents** -- used by `operations-assistant`'s RAG layer, not this repo. Written
  from scratch to sound like real corporate policy; not based on any real company's actual
  documents.
- **"Northstar Manufacturing"** itself -- a fictional client name used to frame the business
  scenario throughout `docs/`.

## Why this matters
Claiming BPI 2019 is "our client's data" would be dishonest and easily caught by anyone who
recognizes the dataset (it's well-known in the process-mining community). Claiming the SLA
targets or process model are real client rules would be a claim this project can't back up. The
honest framing -- real event data, synthetic business rules layered on top, clearly labeled -- is
both accurate and, said plainly in an interview, itself a positive signal: it shows the difference
between "this is what I built" and "this is what I found."
