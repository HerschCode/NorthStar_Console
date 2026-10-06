"""Run a budgeted, reproducibly sampled AgentDojo suite with gateway defenses."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import random
import sys
import uuid
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from gateway.agentdojo import (
    BudgetedPipelineElement,
    CallBudget,
    GatewayAgentDojoDetector,
    RunBudgetExceeded,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an AgentDojo suite with the gateway detector in its tool-output path."
    )
    parser.add_argument("--suite", default="workspace", help="AgentDojo suite (e.g. workspace, slack, travel, banking)")
    parser.add_argument("--attack", default="tool_knowledge", help="Registered AgentDojo attack")
    parser.add_argument("--model", default="gpt-4o-2024-05-13", help="AgentDojo model name")
    parser.add_argument("--model-id", help="Optional local or OpenAI-compatible model id")
    parser.add_argument("--provider", choices=("agentdojo", "ollama"), default="agentdojo")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434/v1")
    parser.add_argument("--tool-delimiter", default="tool", help="Tool delimiter for local AgentDojo models")
    parser.add_argument("--benchmark-version", default="v1.2.2")
    parser.add_argument("--user-task", action="append", dest="user_tasks", help="User task ID; repeat to select multiple")
    parser.add_argument(
        "--injection-task", action="append", dest="injection_tasks", help="Injection task ID; repeat to select multiple"
    )
    parser.add_argument("--logdir", type=Path, default=REPO_ROOT / "runs" / "agentdojo")
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "reports" / "p3_agentdojo.json")
    parser.add_argument("--force-rerun", action="store_true")
    parser.add_argument("--max-pairs", type=int, default=6, help="Maximum user-task/injection-task pairs per arm (default: 6)")
    parser.add_argument("--seed", type=int, default=2026, help="Stable seed for pair sampling")
    parser.add_argument("--smoke-test", action="store_true", help="Cap the run at 3 task pairs")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the selected plan without model calls")
    parser.add_argument("--max-llm-calls", type=int, default=100, help="Hard upper bound on LLM invocations per comparison arm")
    parser.add_argument("--max-cost-usd", type=float, help="Estimated spend ceiling per arm; requires a per-call upper bound")
    parser.add_argument(
        "--estimated-cost-per-llm-call-usd",
        type=float,
        help="Conservative cost reservation per model invocation; set from provider pricing and expected token limits",
    )
    parser.add_argument(
        "--firewall-policy",
        type=Path,
        help="Gateway YAML action policy for AgentDojo tool calls; tools absent from it are denied",
    )
    parser.add_argument(
        "--tool-map",
        type=Path,
        help="Optional JSON object mapping AgentDojo tool names to names in the gateway policy",
    )
    parser.add_argument("--firewall-role", default="employee", help="Gateway principal role used for tool authorization")
    parser.add_argument(
        "--compare-baseline",
        action="store_true",
        help="Also run the same AgentDojo pipeline without the gateway detector (additional model/API cost)",
    )
    return parser


def _summary(results: dict[str, Any]) -> dict[str, Any]:
    security = list(results["security_results"].values())
    utility = list(results["utility_results"].values())
    injection_utility = list(results["injection_tasks_utility_results"].values())
    return {
        "cases": len(security),
        "security_rate": mean(security) if security else None,
        "attack_success_rate": 1.0 - mean(security) if security else None,
        "utility_rate": mean(utility) if utility else None,
        "injection_task_utility_rate": mean(injection_utility) if injection_utility else None,
    }


def _selected_pairs(suite: Any, args: argparse.Namespace, dos_attack: bool = False) -> list[tuple[str, str]]:
    user_ids = list(args.user_tasks or suite.user_tasks.keys())
    injection_ids = list(args.injection_tasks or suite.injection_tasks.keys())
    invalid_users = sorted(set(user_ids) - set(suite.user_tasks))
    invalid_injections = sorted(set(injection_ids) - set(suite.injection_tasks))
    if invalid_users or invalid_injections:
        raise ValueError(f"Unknown task IDs: user_tasks={invalid_users}, injection_tasks={invalid_injections}")
    if dos_attack:
        first_injection = next(iter(suite.injection_tasks), None)
        if first_injection is None:
            raise ValueError("The selected AgentDojo suite has no injection tasks for the DoS attack")
        if args.injection_tasks and first_injection not in args.injection_tasks:
            raise ValueError(
                f"AgentDojo DoS attacks use only {first_injection!r} in this suite; "
                "remove --injection-task or select that task"
            )
        injection_ids = [first_injection]
    pairs = [(user_id, injection_id) for user_id in user_ids for injection_id in injection_ids]
    if not pairs:
        raise ValueError("No user-task/injection-task pairs selected")
    cap = args.max_pairs
    if args.smoke_test:
        cap = min(cap, 3)
    if cap < 1:
        raise ValueError("--max-pairs must be at least one")
    return random.Random(args.seed).sample(pairs, min(cap, len(pairs)))


def _validate_budget_args(args: argparse.Namespace) -> None:
    if args.max_llm_calls < 1:
        raise ValueError("--max-llm-calls must be at least one")
    if args.max_pairs < 1:
        raise ValueError("--max-pairs must be at least one")
    if args.max_cost_usd is not None and args.estimated_cost_per_llm_call_usd is None:
        raise ValueError("--max-cost-usd requires --estimated-cost-per-llm-call-usd")
    if args.estimated_cost_per_llm_call_usd is not None and args.estimated_cost_per_llm_call_usd <= 0:
        raise ValueError("--estimated-cost-per-llm-call-usd must be positive")
    if args.max_cost_usd is not None and args.max_cost_usd <= 0:
        raise ValueError("--max-cost-usd must be positive")
    if args.provider == "ollama" and args.max_cost_usd is not None:
        raise ValueError("Ollama is treated as local/zero-billed; omit cloud cost options for Ollama runs")


def _run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_budget_args(args)
    try:
        from dotenv import load_dotenv
        from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig
        from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM
        from agentdojo.agent_pipeline.tool_execution import ToolsExecutionLoop
        from agentdojo.attacks import load_attack
        from agentdojo.attacks.attack_registry import ATTACKS
        from agentdojo.benchmark import run_task_with_injection_tasks, run_task_without_injection_tasks
        from agentdojo.logging import OutputLogger
        from agentdojo.models import ModelsEnum
        from agentdojo.task_suite.load_suites import get_suite
        from gateway.actions.approvals import ApprovalQueue
        from gateway.actions.firewall import ActionFirewall
        from gateway.actions.policy import Policy, Principal
    except ImportError as exc:
        raise RuntimeError(
            'AgentDojo integration dependencies are missing; install them with `pip install -e ".[agentdojo]"`.'
        ) from exc

    load_dotenv()
    try:
        suite = get_suite(args.benchmark_version, args.suite)
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"Unknown AgentDojo suite/version: {args.suite!r} at {args.benchmark_version!r}"
        ) from exc
    if args.attack not in ATTACKS:
        raise ValueError(f"Unknown AgentDojo attack {args.attack!r}; registered attacks: {sorted(ATTACKS)}")
    pairs = _selected_pairs(suite, args, dos_attack=ATTACKS[args.attack].is_dos_attack)
    if args.provider == "agentdojo":
        try:
            ModelsEnum(args.model)
        except ValueError as exc:
            raise ValueError(f"Unsupported AgentDojo model {args.model!r}") from exc

    tool_map: dict[str, str] = {}
    if args.tool_map:
        loaded_map = json.loads(args.tool_map.read_text(encoding="utf-8"))
        if not isinstance(loaded_map, dict) or any(
            not isinstance(source, str) or not isinstance(target, str)
            for source, target in loaded_map.items()
        ):
            raise ValueError("--tool-map must be a JSON object with string keys and string values")
        tool_map = loaded_map

    cost_per_call = args.estimated_cost_per_llm_call_usd
    if args.max_cost_usd is not None:
        if cost_per_call is None or cost_per_call <= 0:
            raise ValueError("--max-cost-usd requires a positive per-call cost reservation")
        cost_calls = math.floor(args.max_cost_usd / cost_per_call + 1e-12)
        if cost_calls < 1:
            raise ValueError("--max-cost-usd does not cover one estimated model call")
    else:
        cost_calls = args.max_llm_calls
    call_limit = min(args.max_llm_calls, cost_calls)
    estimated_cost_ceiling_per_arm = None
    if args.max_cost_usd is not None:
        estimated_cost_ceiling_per_arm = min(args.max_cost_usd, call_limit * cost_per_call)

    if args.provider == "ollama":
        model_id = args.model_id or "qwen2.5:7b-instruct"
        if cost_per_call is None:
            cost_per_call = 0.0
        report_model, report_model_id = "ollama", model_id
        llm = None
        if not args.dry_run:
            from openai import OpenAI

            client = OpenAI(api_key="ollama", base_url=args.ollama_base_url, max_retries=0)
            llm = OpenAILLM(client, model_id)
            llm.name = ModelsEnum.LOCAL
    else:
        llm = args.model
        report_model, report_model_id = args.model, args.model_id

    if args.dry_run:
        arms = 2 if args.compare_baseline else 1
        return {
            "schema_version": 1,
            "benchmark": "AgentDojo",
            "agentdojo_version": importlib.metadata.version("agentdojo"),
            "benchmark_version": args.benchmark_version,
            "suite": args.suite,
            "attack": args.attack,
            "provider": args.provider,
            "model": report_model,
            "model_id": report_model_id,
            "dry_run": True,
            "selected_pairs": [{"user_task": user, "injection_task": injection} for user, injection in pairs],
            "pair_count_per_arm": len(pairs),
            "comparison_arms": arms,
            "llm_call_budget_per_arm": call_limit,
            "max_total_llm_calls": call_limit * arms,
            "estimated_cost_per_call_usd": cost_per_call,
            "estimated_cost_ceiling_per_arm_usd": estimated_cost_ceiling_per_arm,
            "estimated_total_cost_ceiling_usd": (
                estimated_cost_ceiling_per_arm * arms if estimated_cost_ceiling_per_arm is not None else None
            ),
            "firewall_policy": str(args.firewall_policy) if args.firewall_policy else None,
            "tool_map": tool_map,
            "limitations": [
                "Dry run makes no model calls and does not verify credentials, Ollama availability, or model availability.",
                "Cost is a reservation estimate based on the configured per-call upper bound, not a provider billing guarantee.",
            ],
        }

    def create_pipeline(with_gateway: bool, budget: CallBudget):
        if llm is None:
            raise RuntimeError("Ollama LLM client was not initialized")
        pipeline = AgentPipeline.from_config(
            PipelineConfig(
                llm=llm,
                model_id=args.model_id,
                defense=None,
                tool_delimiter=args.tool_delimiter,
                system_message_name=None,
                system_message=None,
            )
        )
        tool_loop = pipeline.elements[-1]
        if not isinstance(tool_loop, ToolsExecutionLoop):
            raise TypeError("Unsupported AgentDojo API: expected ToolsExecutionLoop as the final pipeline element")
        budgeted_llm = BudgetedPipelineElement(llm if args.provider == "ollama" else pipeline.elements[2], budget)
        pipeline.elements[2] = budgeted_llm
        tool_loop.elements[-1] = budgeted_llm
        detector = GatewayAgentDojoDetector() if with_gateway else None
        firewall_executor = None
        if with_gateway and args.firewall_policy:
            from gateway.agentdojo import GatewayActionFirewallExecutor

            run_id = uuid.uuid4().hex
            firewall = ActionFirewall(
                policy=Policy.load(args.firewall_policy),
                approvals=ApprovalQueue(args.logdir / f"agentdojo-approvals-{run_id}.sqlite"),
                audit_path=args.logdir / f"agentdojo-actions-{run_id}.jsonl",
            )
            firewall_executor = GatewayActionFirewallExecutor(
                firewall, Principal(args.firewall_role, "agentdojo-benchmark"), tool_map
            )
            tool_loop.elements[0] = firewall_executor
        if detector is not None:
            tool_loop.elements.insert(1, detector)
            pipeline.name = f"{pipeline.name}-llm-security-gateway"
        if firewall_executor is not None:
            pipeline.name += "-action-firewall"
        return pipeline, detector, firewall_executor

    runs: dict[str, Any] = {}
    args.logdir.mkdir(parents=True, exist_ok=True)
    with OutputLogger(str(args.logdir)):
        for name, with_gateway in (
            ([("gateway", True), ("baseline", False)] if args.compare_baseline else [("gateway", True)])
        ):
            budget = CallBudget(call_limit, args.max_cost_usd, cost_per_call)
            pipeline, detector, firewall_executor = create_pipeline(with_gateway, budget)
            attack = load_attack(args.attack, suite, pipeline)
            combined = {
                "utility_results": {},
                "security_results": {},
                "injection_tasks_utility_results": {},
            }
            completed_pairs = []
            interrupted_by_budget = None
            try:
                if not attack.is_dos_attack:
                    for injection_id in dict.fromkeys(injection_id for _, injection_id in pairs):
                        injection_task = suite.get_injection_task_by_id(injection_id)
                        successful, _ = run_task_without_injection_tasks(
                            suite,
                            pipeline,
                            injection_task,
                            logdir=args.logdir,
                            force_rerun=args.force_rerun,
                            benchmark_version=args.benchmark_version,
                        )
                        combined["injection_tasks_utility_results"][injection_id] = successful

                for user_id, injection_id in pairs:
                    user_task = suite.get_user_task_by_id(user_id)
                    utility_results, security_results = run_task_with_injection_tasks(
                        suite,
                        pipeline,
                        user_task,
                        attack,
                        logdir=args.logdir,
                        force_rerun=args.force_rerun,
                        injection_tasks=[injection_id],
                        benchmark_version=args.benchmark_version,
                    )
                    combined["utility_results"].update(utility_results)
                    combined["security_results"].update(security_results)
                    completed_pairs.append({"user_task": user_id, "injection_task": injection_id})
            except RunBudgetExceeded as exc:
                interrupted_by_budget = str(exc)

            runs[name] = {
                "pipeline": pipeline.name,
                "metrics": _summary(combined),
                "requested_pairs": len(pairs),
                "completed_pairs": completed_pairs,
                "complete": interrupted_by_budget is None and len(completed_pairs) == len(pairs),
                "budget_stop_reason": interrupted_by_budget,
                "budget": budget.snapshot(),
                "detector": detector.stats() if detector is not None else None,
                "action_firewall": firewall_executor.stats() if firewall_executor is not None else None,
            }
            if interrupted_by_budget:
                break

    return {
        "schema_version": 1,
        "benchmark": "AgentDojo",
        "agentdojo_version": importlib.metadata.version("agentdojo"),
        "benchmark_version": args.benchmark_version,
        "suite": args.suite,
        "attack": args.attack,
        "provider": args.provider,
        "model": report_model,
        "model_id": report_model_id,
        "firewall_policy": str(args.firewall_policy) if args.firewall_policy else None,
        "tool_map": tool_map,
        "seed": args.seed,
        "max_pairs": args.max_pairs,
        "smoke_test": args.smoke_test,
        "selected_pairs": [{"user_task": user, "injection_task": injection} for user, injection in pairs],
        "max_llm_calls_per_arm": call_limit,
        "max_cost_usd_per_arm": args.max_cost_usd,
        "estimated_cost_per_llm_call_usd": cost_per_call,
        "comparison_arms": 2 if args.compare_baseline else 1,
        "max_total_llm_calls": call_limit * (2 if args.compare_baseline else 1),
        "estimated_total_cost_ceiling_usd": (
            estimated_cost_ceiling_per_arm * (2 if args.compare_baseline else 1)
            if estimated_cost_ceiling_per_arm is not None else None
        ),
        "runs": runs,
        "limitations": [
            (
                "No action firewall ran; this is a detector-only benchmark."
                if not args.firewall_policy
                else "Action-firewall results depend on the supplied policy and tool mapping; unmapped tools are denied."
            ),
            "AgentDojo's simulated tools and task utilities are unchanged.",
            "The benchmark is stochastic and model/provider dependent; preserve the exact model, suite version and run logs when comparing.",
            "Cost limits reserve a user-supplied per-call amount and are not a provider-enforced billing cap; use provider quotas/budgets as an additional control.",
        ],
    }


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = _run(args)
    except (ImportError, OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report.get("dry_run"):
        print(
            f"DRY RUN: {report['pair_count_per_arm']} pairs/arm; "
            f"at most {report['max_total_llm_calls']} model calls total"
        )
    else:
        for name, result in report["runs"].items():
            metrics = result["metrics"]
            print(
                f"{name}: cases={metrics['cases']}, security={metrics['security_rate']}, "
                f"attack_success={metrics['attack_success_rate']}, utility={metrics['utility_rate']}, "
                f"llm_calls={result['budget']['llm_calls']}, complete={result.get('complete', True)}"
            )
            if result.get("budget_stop_reason"):
                print(f"{name}: stopped: {result['budget_stop_reason']}")
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
