"""
Accounts-payable controls tools — wrappers around operations-performance's
/controls/* and /working-capital/* endpoints (added in the finance module, P1 Phase F4).

Same pattern as analytics.py: narrow, parameterised, row-capped, never writes.
Each function raises OpsPerformanceUnavailable on a downstream failure; the agent
layer catches it and responds gracefully rather than crashing the turn.
"""
from src.tools.client import get
from src.tools.validation import clamp_int

# ── Read tools ────────────────────────────────────────────────────────────────

GET_CONTROL_EXCEPTIONS_SCHEMA = {
    "name": "get_control_exceptions",
    "description": (
        "Return accounts-payable control exceptions from the finance controls layer. "
        "Each exception has a control ID (C1–C6), severity, EUR exposure, vendor, and evidence fields. "
        "C1=three-way-match violation, C2=invoice before goods receipt, C3=approval-threshold split, "
        "C4=duplicate invoice, C5=payment-block override without segregation of duties, "
        "C6=Benford's law anomaly. "
        "Filters are all optional: omit to get all open exceptions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "control": {
                "type": "string",
                "description": "Filter to a specific control ID: 'C1', 'C2', 'C3', 'C4', 'C5', or 'C6'.",
            },
            "vendor": {
                "type": "string",
                "description": "Filter to a specific vendor name or ID.",
            },
            "min_exposure": {
                "type": "number",
                "description": "Minimum EUR exposure to include. Use to focus on high-value exceptions.",
            },
            "top_n": {
                "type": "integer",
                "description": "Maximum number of exceptions to return (default 20, max 100).",
                "default": 20,
            },
        },
        "required": [],
    },
}


def get_control_exceptions(
    control: str | None = None,
    vendor: str | None = None,
    min_exposure: float | None = None,
    top_n: int = 20,
) -> list:
    top_n = clamp_int(top_n, max_value=100)
    params: dict = {"top_n": top_n}
    if control:
        params["control"] = control
    if vendor:
        params["vendor"] = vendor
    if min_exposure is not None:
        params["min_exposure"] = min_exposure
    return get("/controls/exceptions", params=params)


GET_CONTROL_SUMMARY_SCHEMA = {
    "name": "get_control_summary",
    "description": (
        "Return a summary of AP control exceptions by control ID: count, total EUR exposure, "
        "and severity breakdown. Use this for a high-level view before drilling into individual "
        "exceptions with get_control_exceptions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def get_control_summary() -> dict:
    return get("/controls/summary")


GET_WORKING_CAPITAL_SUMMARY_SCHEMA = {
    "name": "get_working_capital_summary",
    "description": (
        "Return working-capital metrics: days payable outstanding (overall and by vendor/spend area), "
        "invoice-to-clear cycle time, late-payment exposure in EUR, and early-payment discount "
        "capture rate (2/10 net 30 scenario). "
        "Use to answer questions about payment timing, cash flow, or discount opportunities."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def get_working_capital_summary() -> dict:
    return get("/working-capital/summary")


# ── Write tools (proposals only, never executed without approval) ─────────────

PROPOSE_PAYMENT_HOLD_SCHEMA = {
    "name": "propose_payment_hold",
    "description": (
        "Propose placing a payment hold on a specific case or invoice. "
        "The hold is NOT executed immediately — it enters the approval queue and requires "
        "a manager to approve it via POST /interventions/{id}/approve. "
        "Use when an AP exception (e.g. three-way-match break, duplicate invoice, SOD violation) "
        "indicates payment should be paused pending review. "
        "Include the specific control ID and evidence in the reason."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "case_id": {
                "type": "string",
                "description": "The case or document ID to hold.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Reason for the hold, including the control ID (e.g. 'C4 duplicate invoice: "
                    "same vendor, same amount EUR 12,450, within 30 days of invoice 2024-089') "
                    "and any relevant evidence fields."
                ),
            },
        },
        "required": ["case_id", "reason"],
    },
}


def propose_payment_hold(case_id: str, reason: str) -> dict:
    from src.tools.interventions import propose_intervention
    return propose_intervention(
        action="hold_payment",
        target=case_id,
        reason=reason,
        priority="high",
    )


PROPOSE_PAYMENT_RELEASE_SCHEMA = {
    "name": "propose_payment_release",
    "description": (
        "Propose releasing a payment that is currently on hold. "
        "The release is NOT executed immediately — it enters the approval queue and requires "
        "a manager (not the same person who proposed it) to approve via POST /interventions/{id}/approve. "
        "Separation of duties is enforced: the requester cannot be the approver. "
        "Use when a hold has been reviewed and the business justification supports release."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "case_id": {
                "type": "string",
                "description": "The case or document ID to release.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Justification for release: which exception was resolved, how, "
                    "and who authorised the resolution. Cite the relevant policy clause."
                ),
            },
        },
        "required": ["case_id", "reason"],
    },
}


def propose_payment_release(case_id: str, reason: str) -> dict:
    from src.tools.interventions import propose_intervention
    return propose_intervention(
        action="release_payment",
        target=case_id,
        reason=reason,
        priority="normal",
    )


ALL_AP_TOOLS = [
    (GET_CONTROL_EXCEPTIONS_SCHEMA, get_control_exceptions),
    (GET_CONTROL_SUMMARY_SCHEMA, get_control_summary),
    (GET_WORKING_CAPITAL_SUMMARY_SCHEMA, get_working_capital_summary),
    (PROPOSE_PAYMENT_HOLD_SCHEMA, propose_payment_hold),
    (PROPOSE_PAYMENT_RELEASE_SCHEMA, propose_payment_release),
]
