---
title: Segregation of Duties Matrix — Procure-to-Pay
version: "1.0"
effective_date: 2024-03-01
department: Finance / Internal Controls
---

# 1. Purpose
This document defines which roles at Northstar Manufacturing (fictional company — see the parent
project's docs/data-contract.md) may perform each activity in the Procure-to-Pay process, and
identifies which combinations of activities are incompatible (i.e., must not be performed by the
same person on the same document). It is a companion to the Accounts Payable Controls Policy and
the Procurement Policy.

# 2. Defined Roles

| Role | Description |
|---|---|
| **Requester** | Department employee who originates a purchase requisition. |
| **Procurement Officer** | Procurement team member who creates and manages purchase orders. |
| **Goods Receiver** | Warehouse or operations staff who records goods receipts. |
| **AP Clerk** | Accounts Payable staff who records and processes invoices. |
| **AP Supervisor** | Senior AP staff with authority to review and release payment blocks. |
| **AP Manager** | AP team manager with authority to approve exceptions, remove blocks, and escalate. |
| **Finance Director** | Approves emergency payment releases and material exceptions. |
| **Internal Audit** | Reviews exceptions; has no operational authority to approve or release. |
| **System (automated)** | Automated system actions: block creation, duplicate alerts, threshold alerts. |

A single person may hold multiple roles (e.g., AP Supervisor and AP Clerk) only if explicitly
approved by the Finance Director and documented in the HR system.

# 3. Activity Permissions

| Activity | Requester | Procurement Officer | Goods Receiver | AP Clerk | AP Supervisor | AP Manager | Finance Director |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Create Purchase Requisition | ✓ | — | — | — | — | — | — |
| Approve Purchase Requisition (≤$10k) | — | ✓ | — | — | — | ✓ | ✓ |
| Approve Purchase Requisition (>$10k, secondary) | — | — | — | — | — | ✓ | ✓ |
| Create Purchase Order | — | ✓ | — | — | — | — | — |
| Amend Purchase Order | — | ✓ | — | — | — | ✓ | ✓ |
| Record Goods Receipt | — | — | ✓ | — | — | — | — |
| Create / Record Invoice | — | — | — | ✓ | — | — | — |
| Apply Payment Block (automated) | — | — | — | — | — | — | — |
| Remove Payment Block | — | — | — | — | ✓ | ✓ | ✓ |
| Clear Invoice (execute payment) | — | — | — | ✓ | — | — | — |
| Release Emergency Payment | — | — | — | — | — | — | ✓ |
| Approve Duplicate-Alert Resolution | — | — | — | — | ✓ | ✓ | — |

Legend: ✓ = permitted, — = not permitted by default. Exceptions require written approval from the Finance Director.

# 4. Incompatible Activity Combinations

The following combinations are **prohibited** for the same person on the same document:

| # | Combination | Reason |
|---|---|---|
| SOD-01 | Create PO **and** Record Goods Receipt | Prevents fictitious PO + false GR |
| SOD-02 | Create PO **and** Record/Clear Invoice | Prevents ordering and paying without independent receiving |
| SOD-03 | Record Goods Receipt **and** Create/Clear Invoice | Prevents false GR used to release payment |
| SOD-04 | Create Invoice **and** Remove Payment Block (same document) | Ensures a second person verifies before payment |
| SOD-05 | Create Invoice **and** Clear Invoice (same document) | Four-eyes on invoice creation and payment execution |
| SOD-06 | Create/Approve Purchase Requisition **and** Clear Invoice (same document) | End-to-end control: requester cannot also pay |
| SOD-07 | Raise Duplicate Alert (System) **and** Approve Duplicate-Alert Resolution (same document) | Alerts are system-generated; resolution must be human-reviewed |

# 5. Segregation-of-Duties Checks in the AP Controls System

The Procure-to-Pay system enforces SOD-04 at the transaction level: a user who recorded the invoice
for a document cannot execute "Remove Payment Block" on that same document. A system alert is
generated if this is attempted and logged as a control exception.

SOD-01, SOD-02, SOD-03, SOD-05, SOD-06 are enforced by role permissions; role assignments are
reviewed quarterly by Internal Audit.

SOD-07 is enforced programmatically: the duplicate-alert resolution workflow requires a different
user than the system account that raised the alert.

# 6. Compensating Controls
Where full segregation is impractical (for example, in a small regional office with limited staff),
the following compensating controls apply:
- Monthly management review of all payment-block removals and invoice-to-clear activities by a
  person outside the AP team.
- Automated weekly report to the Finance Director of any SOD-01 through SOD-07 violations.
- Internal Audit review of compensating-control offices at least annually.

# 7. Exception Approval
An individual who requires temporary access to an incompatible combination (for example, to cover
an absent colleague) must obtain written approval from both the AP Manager and the Finance Director
before the access is granted. The exception must be logged, time-limited (maximum 5 business days),
and reviewed by Internal Audit at the next scheduled review.

# 8. Enforcement and Review
This matrix is reviewed annually by Internal Audit. Role assignments are updated in the HR system
and must match the permissions in Section 3. Any discrepancy between the HR system and the
Procure-to-Pay role table is a control exception to be resolved within 10 business days.
