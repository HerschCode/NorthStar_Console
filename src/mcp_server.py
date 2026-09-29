"""
MCP server exposing all 14 operations-assistant tools.

Each tool delegates to the existing implementation in src/tools/, which means:
  - The same input validation (validate_case_id, validate_query, clamp_int) runs for
    every call regardless of whether the caller is the built-in agent or an external
    MCP client.
  - OpsPerformanceUnavailable propagates as an unhandled exception; MCPServer converts
    that into an MCP error response, so the caller gets a typed error, not a crash.

Transport:
  stdio (default)   -- for Claude Desktop / mcp CLI
  sse               -- HTTP+SSE for remote clients
  streamable-http   -- HTTP streaming (mcp 2.x preferred for remote)

Usage:
  python -m src.mcp_server                                   # stdio
  python -m src.mcp_server --transport sse --port 8081       # SSE on port 8081
  mcp dev src/mcp_server.py                                  # MCP Inspector (stdio)
"""
import argparse
import json

from contextlib import contextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from src.tools.analytics import (
    get_cycle_time as _get_cycle_time,
    get_bottlenecks as _get_bottlenecks,
    get_sla_metrics as _get_sla_metrics,
    get_supplier_performance as _get_supplier_performance,
    get_management_report as _get_management_report,
)
from src.tools.prediction import predict_sla_risk as _predict_sla_risk
from src.tools.database import get_pipeline_status as _get_pipeline_status
from src.tools.documents import search_policy_documents as _search_policy_documents
from src.tools.process import get_conformance as _get_conformance
from src.tools.interventions import propose_intervention as _propose_intervention
from src.tools.ap_controls import (
    get_control_exceptions as _get_control_exceptions,
    get_control_summary as _get_control_summary,
    get_working_capital_summary as _get_working_capital_summary,
    propose_payment_hold as _propose_payment_hold,
    propose_payment_release as _propose_payment_release,
)
from src.tools.client import OpsPerformanceUnavailable

mcp = MCPServer(
    "operations-assistant",
    description=(
        "Procurement operations analytics: cycle time, SLA metrics, bottlenecks, "
        "supplier performance, SLA-risk prediction, pipeline health, and policy search."
    ),
    version="1.0.0",
)


def _json(obj) -> str:
    return json.dumps(obj, default=str, indent=2)


@contextmanager
def _tool_errors():
    """Convert anticipated tool failures to ToolError so clients get typed MCP errors.

    ValueError comes from input validation (validate_case_id, validate_query, clamp_int).
    OpsPerformanceUnavailable comes from the HTTP client layer.
    Both are expected in normal operation, not crashes — ToolError is the right type.
    """
    try:
        yield
    except (ValueError, OpsPerformanceUnavailable) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    description=(
        "Get procurement cycle time statistics (mean, median, p90). "
        "Omit segment for overall stats; pass segment='category' for a per-category breakdown."
    )
)
def get_cycle_time(segment: str | None = None) -> str:
    with _tool_errors():
        return _json(_get_cycle_time(segment=segment))


@mcp.tool(
    description=(
        "Get the slowest process stages (bottlenecks), ranked by average duration "
        "and percent of total delay."
    )
)
def get_bottlenecks(top_n: int = 10) -> str:
    with _tool_errors():
        return _json(_get_bottlenecks(top_n=top_n))


@mcp.tool(
    description=(
        "Get SLA breach rate and case counts, optionally broken down by a segment "
        "(e.g. 'category')."
    )
)
def get_sla_metrics(segment: str | None = None) -> str:
    with _tool_errors():
        return _json(_get_sla_metrics(segment=segment))


@mcp.tool(
    description=(
        "Get the supplier performance scorecard (cycle time, SLA breach rate, order count) "
        "for suppliers with enough order volume to rank reliably."
    )
)
def get_supplier_performance(min_volume: int = 5, top_n: int = 15) -> str:
    with _tool_errors():
        return _json(_get_supplier_performance(min_volume=min_volume, top_n=top_n))


@mcp.tool(
    description=(
        "Get the current Finding → Evidence → Impact → Recommendation management report, "
        "generated fresh from live data."
    )
)
def get_management_report() -> str:
    with _tool_errors():
        return _get_management_report()


@mcp.tool(
    description=(
        "Predict whether a specific procurement case is at risk of breaching its SLA, "
        "with a risk level and probability."
    )
)
def predict_sla_risk(case_id: str) -> str:
    with _tool_errors():
        return _json(_predict_sla_risk(case_id=case_id))


@mcp.tool(
    description=(
        "Check the status and timing of recent data pipeline runs, to confirm the "
        "underlying data is current before trusting a metric."
    )
)
def get_pipeline_status(limit: int = 5) -> str:
    with _tool_errors():
        return _json(_get_pipeline_status(limit=limit))


@mcp.tool(
    description=(
        "Search the company's 12 policy and procedure documents (procurement, SLA, escalation, "
        "exception handling, vendor onboarding, quality control, inventory, contract renewal, "
        "data retention, safety, AP controls, and segregation of duties) for relevant sections. "
        "Use for questions about rules, approval requirements, or procedures — "
        "not for operational numbers, which need get_cycle_time / get_sla_metrics / etc."
    )
)
def search_policy_documents(query: str) -> str:
    with _tool_errors():
        return _json(_search_policy_documents(query=query))


@mcp.tool(
    description=(
        "Get the process conformance rate — what fraction of cases follow the expected "
        "Procure-to-Pay sequence, and a breakdown of deviation types."
    )
)
def get_conformance() -> str:
    with _tool_errors():
        return _json(_get_conformance())


@mcp.tool(
    description=(
        "Propose a human-in-the-loop intervention for a procurement action that requires "
        "operator approval before execution. Returns an intervention_id that an operator "
        "must approve via the REST API (POST /interventions/{id}/approve). "
        "Valid actions: escalate_case, flag_supplier, notify_manager, request_approval, mark_exception, "
        "hold_payment, release_payment. "
        "Valid priorities: low, normal, high, urgent. "
        "ROI context is attached server-side from operations-performance; do not estimate it."
    )
)
def propose_intervention(
    action: str,
    target: str,
    reason: str,
    priority: str = "normal",
) -> str:
    with _tool_errors():
        return _json(_propose_intervention(action=action, target=target, reason=reason, priority=priority))


@mcp.tool(
    description=(
        "Return AP control exceptions from the finance controls layer. "
        "Controls: C1=three-way-match violation, C2=invoice before GR, C3=approval-threshold split, "
        "C4=duplicate invoice, C5=payment-block override without SoD, C6=Benford's law anomaly. "
        "All filters are optional; omit to get all open exceptions."
    )
)
def get_control_exceptions(
    control: str | None = None,
    vendor: str | None = None,
    min_exposure: float | None = None,
    top_n: int = 20,
) -> str:
    with _tool_errors():
        return _json(_get_control_exceptions(
            control=control, vendor=vendor, min_exposure=min_exposure, top_n=top_n
        ))


@mcp.tool(
    description=(
        "Return a high-level summary of AP control exceptions by control ID: "
        "count, total EUR exposure, and severity breakdown. "
        "Use this before drilling into individual exceptions with get_control_exceptions."
    )
)
def get_control_summary() -> str:
    with _tool_errors():
        return _json(_get_control_summary())


@mcp.tool(
    description=(
        "Return working-capital metrics: days payable outstanding (overall and by vendor/spend area), "
        "invoice-to-clear cycle time, late-payment exposure in EUR, and early-payment discount capture rate. "
        "Use for questions about payment timing, cash flow, or 2/10 net 30 discount opportunities."
    )
)
def get_working_capital_summary() -> str:
    with _tool_errors():
        return _json(_get_working_capital_summary())


@mcp.tool(
    description=(
        "Propose placing a payment hold on a specific case or invoice for human approval. "
        "The hold is NOT executed immediately — a manager must approve it via "
        "POST /interventions/{id}/approve. "
        "Use when an AP exception (C1–C6) indicates payment should be paused. "
        "Include the control ID and evidence in the reason."
    )
)
def propose_payment_hold(case_id: str, reason: str) -> str:
    with _tool_errors():
        return _json(_propose_payment_hold(case_id=case_id, reason=reason))


@mcp.tool(
    description=(
        "Propose releasing a payment that is currently on hold, for human approval. "
        "The release is NOT executed immediately — a manager (not the proposer) must approve via "
        "POST /interventions/{id}/approve. "
        "Include which exception was resolved, how, and who authorised the resolution."
    )
)
def propose_payment_release(case_id: str, reason: str) -> str:
    with _tool_errors():
        return _json(_propose_payment_release(case_id=case_id, reason=reason))


def main():
    parser = argparse.ArgumentParser(
        description="Run the operations-assistant MCP server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport to use",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8081,
        help="Port for SSE / streamable-http transport (ignored for stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for SSE / streamable-http transport",
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
