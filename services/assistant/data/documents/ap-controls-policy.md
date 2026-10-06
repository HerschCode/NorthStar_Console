---
title: Accounts Payable Controls Policy
version: "1.1"
effective_date: 2024-03-01
department: Finance / Accounts Payable
---

# 1. Purpose
This policy defines the controls Northstar Manufacturing (fictional company — see the parent
project's docs/data-contract.md) applies to the accounts payable process to ensure that
invoices are paid only for goods and services that were ordered, received, and correctly
priced, and that payment authority is exercised by authorised personnel in accordance with the
Segregation of Duties Matrix.

# 2. Scope
Applies to all supplier invoices, credit notes, and payment releases processed through the
Procure-to-Pay system. Interacts with the Procurement Policy (approval thresholds), the
Escalation Procedure (supplier disputes), and the Segregation of Duties Matrix (role assignments).

# 3. Three-Way Match Requirement

## 3.1 Standard Three-Way Match
Before an invoice is approved for payment, the AP team must verify that:
- A valid, approved Purchase Order exists for the goods or services.
- A Goods Receipt (GR) has been recorded confirming delivery of the ordered quantity.
- The invoice amount does not exceed the PO amount by more than the tolerance defined in Section 3.3.

All three conditions must be satisfied before the "Clear Invoice" activity is performed.

## 3.2 Two-Way Match (Exceptions)
Services and consignment items that cannot generate a goods receipt are processed under a
two-way match (PO and invoice only). Eligibility for two-way match must be pre-approved by the
AP Manager and documented on the purchase requisition. Consignment items are identified by
their item category in the purchasing system; see the Procurement Policy for item-category definitions.

## 3.3 Invoice Tolerance
An invoice may exceed the approved PO value by up to 3% or EUR 150, whichever is lower,
without triggering a block. Amounts outside this tolerance create a payment block (see Section 5)
and must be resolved before payment.

## 3.4 Invoice before Goods Receipt
For items subject to three-way match, recording an invoice before a goods receipt is a control
exception. The invoice must be placed on payment block automatically and reviewed by the AP
Manager before it can be cleared. The exception must be documented with a business justification.

# 4. Duplicate Invoice Prevention

## 4.1 Duplicate Check Criteria
An invoice is flagged as a potential duplicate if it matches an existing invoice on all of:
- Same vendor number.
- Same invoice amount (within EUR 1 tolerance).
- Invoice date within 30 calendar days of the existing invoice date.

A system alert is raised; payment is blocked pending AP team review.

## 4.2 Resolution
The AP team must confirm whether the invoice is a genuine duplicate (void it) or a legitimate
separate invoice (document the business justification and release the block). The same person who
raised the duplicate alert may not release the block — this is a segregation-of-duties requirement.

# 5. Payment Blocks

## 5.1 When Blocks Are Applied
A payment block is applied automatically when:
- The invoice exceeds the three-way-match tolerance (Section 3.3).
- An invoice is recorded before a goods receipt for a three-way-match item (Section 3.4).
- A duplicate invoice alert fires (Section 4.1).
- The supplier has an outstanding dispute or credit note under review.
- The invoice is from a vendor whose Vendor Risk Score has fallen below the threshold defined in the Procurement Policy.

## 5.2 Who May Remove a Payment Block
Only an AP Supervisor or AP Manager may execute "Remove Payment Block". The person who created
the purchase order, requested the goods, or recorded the invoice for the same document may not
remove the payment block on that same document. This is a segregation-of-duties requirement;
see the Segregation of Duties Matrix for the complete role-permission table.

## 5.3 Required Documentation
Every payment-block removal must include a written justification referencing the specific
exception that triggered the block and confirming the resolution. Removals without documentation
are a control exception reportable to Internal Audit.

## 5.4 Emergency Payment Release
If a business-critical supplier payment must be released before a block can be fully resolved
(for example, to prevent a production stoppage), the AP Manager must obtain written approval
from the Finance Director before removing the block. The Exception Handling Procedure applies.

# 6. Approval-Threshold Controls

## 6.1 Splitting Prohibition
Intentionally splitting a purchase into multiple purchase orders to avoid the $10,000 secondary-
approval threshold defined in the Procurement Policy is prohibited. Indicators of splitting include:
- Multiple purchase orders to the same vendor from the same requester within 7 calendar days,
  where each order is below $10,000 but the sum exceeds $10,000.
- A single delivery or invoice number referencing multiple purchase orders that individually
  are below threshold.

When splitting is suspected, the AP Manager must escalate to Internal Audit within 2 business days.

## 6.2 Cumulative Order Value Tracking
The Procure-to-Pay system accumulates the EUR value of orders per vendor per requester over
rolling 7-day and 30-day windows. Alerts are generated when the cumulative value crosses
the secondary-approval threshold.

# 7. Benford's Law Screening
The AP team runs a first-digit frequency analysis (Benford's law) on invoice amounts by vendor
each quarter for vendors with 10 or more invoices in the period. A chi-square statistic or
Mean Absolute Deviation (MAD) above the alert threshold is a screening signal warranting
further review by Internal Audit. This is an anomaly indicator, not evidence of fraud.

# 8. Reporting
The AP Controls dashboard reports: open payment blocks by reason, unresolved duplicate alerts,
three-way-match exception count, and approval-threshold alerts. The AP Manager reviews this
dashboard weekly and reports monthly to the Finance Director. Material exceptions are escalated
to Internal Audit within 5 business days.

# 9. Limitations
All controls in this policy operate on process data (event logs, amounts, dates). They detect
anomalies relative to documented rules. They do not detect all forms of fraud, errors of
omission, or collusion between multiple authorised users. Exception counts are reported as
anomalies; they cannot be characterised as fraud without independent investigation.
