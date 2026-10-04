"""Optional AgentDojo pipeline element backed by the gateway's injection detector."""
from __future__ import annotations

import threading
import uuid
from ast import literal_eval
from collections.abc import Sequence
from typing import Any

try:
    from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement as _BasePipelineElement
    from agentdojo.agent_pipeline.tool_execution import ToolsExecutor as _ToolsExecutor
    from agentdojo.functions_runtime import EmptyEnv as _EmptyEnv
except ModuleNotFoundError as exc:
    if exc.name != "agentdojo":
        raise

    class _BasePipelineElement:
        pass

    class _ToolsExecutor:
        def __init__(self, tool_output_formatter=None) -> None:
            self.output_formatter = tool_output_formatter or str

    class _EmptyEnv:
        pass


_OMITTED_TEXT = "<Data omitted because the gateway detected a prompt injection>"
_SESSION_ARG = "_llm_security_gateway_session_id"


class RunBudgetExceeded(RuntimeError):
    """Raised before an LLM request would exceed the configured run budget."""


class CallBudget:
    def __init__(
        self,
        max_calls: int,
        max_cost_usd: float | None = None,
        cost_per_call_usd: float | None = None,
    ):
        if max_calls < 1:
            raise ValueError("max_calls must be at least one")
        if max_cost_usd is not None and max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")
        if cost_per_call_usd is not None and cost_per_call_usd < 0:
            raise ValueError("cost_per_call_usd cannot be negative")
        if max_cost_usd is not None and (cost_per_call_usd is None or cost_per_call_usd <= 0):
            raise ValueError("a positive cost_per_call_usd is required with max_cost_usd")
        self.max_calls = max_calls
        self.max_cost_usd = max_cost_usd
        self.cost_per_call_usd = cost_per_call_usd
        self.calls = 0

    def reserve_call(self) -> None:
        if self.calls >= self.max_calls:
            raise RunBudgetExceeded(f"LLM call limit reached ({self.max_calls})")
        if self.cost_per_call_usd is not None:
            estimated_after_call = (self.calls + 1) * self.cost_per_call_usd
            if self.max_cost_usd is not None and estimated_after_call > self.max_cost_usd + 1e-12:
                raise RunBudgetExceeded(
                    f"estimated cost cap reached (${self.max_cost_usd:.4f}); "
                    f"next call is budgeted at ${estimated_after_call:.4f}"
                )
        self.calls += 1

    def snapshot(self) -> dict[str, Any]:
        estimated = (
            round(self.calls * self.cost_per_call_usd, 6)
            if self.cost_per_call_usd is not None
            else None
        )
        return {
            "llm_calls": self.calls,
            "max_llm_calls": self.max_calls,
            "estimated_cost_usd": estimated,
            "max_cost_usd": self.max_cost_usd,
            "estimated_cost_per_call_usd": self.cost_per_call_usd,
        }


class BudgetedPipelineElement(_BasePipelineElement):
    """Reserve a configured call/cost budget before delegating to an LLM."""

    def __init__(self, element: Any, budget: CallBudget) -> None:
        self.element = element
        self.budget = budget
        self.name = getattr(element, "name", None)

    def query(
        self,
        query: str,
        runtime: Any,
        env: Any = _EmptyEnv(),
        messages: Sequence[dict[str, Any]] = (),
        extra_args: dict[str, Any] | None = None,
    ) -> tuple[str, Any, Any, Sequence[dict[str, Any]], dict[str, Any]]:
        self.budget.reserve_call()
        return self.element.query(query, runtime, env, messages, extra_args or {})


def _session_context(extra_args: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    call_args = dict(extra_args or {})
    session_id = call_args.get(_SESSION_ARG)
    if not isinstance(session_id, str) or not session_id:
        session_id = f"agentdojo-{uuid.uuid4().hex}"
        call_args[_SESSION_ARG] = session_id
    return session_id, call_args


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            text = block.get("content")
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts)


def _omit_text(content: Any) -> Any:
    if isinstance(content, str):
        return _OMITTED_TEXT
    if isinstance(content, list):
        return [
            {
                **block,
                "content" if "content" in block and "text" not in block else "text": _OMITTED_TEXT,
            }
            if isinstance(block, dict) and block.get("type") == "text"
            else block
            for block in content
        ]
    return [{"type": "text", "content": _OMITTED_TEXT}]


class GatewayActionFirewallExecutor(_ToolsExecutor):
    """Authorize simulated AgentDojo tool calls through the gateway policy.

    Unknown or unmapped tool names follow the gateway's default-deny policy.
    Calls requiring human approval are returned as held and are never executed
    by this benchmark executor.
    """

    def __init__(self, firewall: Any, principal: Any, tool_map: dict[str, str] | None = None) -> None:
        super().__init__()
        self.firewall = firewall
        self.principal = principal
        self.tool_map = tool_map or {}
        self._stats_lock = threading.Lock()
        self._counts = {"allowed": 0, "denied": 0, "held": 0, "executed": 0}

    @staticmethod
    def _tool_result(tool_call: Any, text: str, error: str | None = None) -> dict[str, Any]:
        return {
            "role": "tool",
            "content": [{"type": "text", "content": text}],
            "tool_call_id": getattr(tool_call, "id", None),
            "tool_call": tool_call,
            "error": error,
        }

    def query(
        self,
        query: str,
        runtime: Any,
        env: Any = _EmptyEnv(),
        messages: Sequence[dict[str, Any]] = (),
        extra_args: dict[str, Any] | None = None,
    ) -> tuple[str, Any, Any, Sequence[dict[str, Any]], dict[str, Any]]:
        if not messages or messages[-1].get("role") != "assistant":
            return query, runtime, env, messages, extra_args if extra_args is not None else {}

        assistant_message = messages[-1]
        tool_calls = assistant_message.get("tool_calls")
        if not tool_calls:
            return query, runtime, env, messages, extra_args if extra_args is not None else {}

        session_id, call_args = _session_context(extra_args)
        if not call_args.get("_llm_security_gateway_task_registered"):
            self.firewall.register_source(session_id, "agentdojo_user_task", query, "trusted")
            call_args["_llm_security_gateway_task_registered"] = True

        known_tools = {tool.name for tool in runtime.functions.values()}
        tool_results = []
        for tool_call in tool_calls:
            tool_name = getattr(tool_call, "function", None)
            args = getattr(tool_call, "args", None)
            if not isinstance(tool_name, str) or not isinstance(args, dict):
                tool_results.append(self._tool_result(tool_call, "Invalid tool call: expected a tool name and object arguments.", "invalid tool call"))
                with self._stats_lock:
                    self._counts["denied"] += 1
                continue

            if tool_name not in known_tools:
                error = f"Invalid tool {tool_name!r} provided."
                tool_results.append(self._tool_result(tool_call, error, error))
                with self._stats_lock:
                    self._counts["denied"] += 1
                continue

            args = dict(args)
            for arg_name, arg_value in args.items():
                if isinstance(arg_value, str):
                    try:
                        parsed = literal_eval(arg_value)
                    except (ValueError, SyntaxError):
                        continue
                    if isinstance(parsed, list):
                        args[arg_name] = parsed

            policy_tool = self.tool_map.get(tool_name, tool_name)
            decision = self.firewall.authorize(session_id, self.principal, policy_tool, args, source="agentdojo")
            with self._stats_lock:
                count_key = {"allow": "allowed", "deny": "denied", "require_approval": "held"}.get(
                    decision.effect, "denied"
                )
                self._counts[count_key] += 1

            if decision.effect == "allow":
                result, error = runtime.run_function(env, tool_name, args)
                formatted_result = self.output_formatter(result)
                tool_results.append(self._tool_result(tool_call, formatted_result, error))
                self.firewall.observe_result(session_id, policy_tool, formatted_result)
                with self._stats_lock:
                    self._counts["executed"] += 1
            elif decision.effect == "require_approval":
                text = f"Held for human approval (id {decision.approval_id}); this action was not executed."
                tool_results.append(self._tool_result(tool_call, text, text))
            else:
                text = "Blocked by the gateway action firewall: " + "; ".join(decision.reasons)
                tool_results.append(self._tool_result(tool_call, text, text))

        return query, runtime, env, [*messages, *tool_results], call_args

    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            return dict(self._counts)


class GatewayAgentDojoDetector(_BasePipelineElement):
    """Screen AgentDojo tool results before they are returned to the LLM.

    Insert this element after AgentDojo's ``ToolsExecutor`` and before the
    LLM in a ``ToolsExecutionLoop``. It follows AgentDojo's detector contract:
    detected text is omitted, while the rest of the simulated tool result is
    preserved. A middleware instance is created lazily so importing this
    integration does not load optional models or ONNX Runtime.
    """

    name = "llm-security-gateway"

    def __init__(self, middleware: Any | None = None) -> None:
        self._middleware = middleware
        self._stats_lock = threading.Lock()
        self._scanned_messages = 0
        self._blocked_messages = 0
        self._blocked_layers: dict[str, int] = {}

    def _get_middleware(self):
        if self._middleware is None:
            from gateway.middleware import GatewayMiddleware

            self._middleware = GatewayMiddleware()
        return self._middleware

    def query(
        self,
        query: str,
        runtime: Any,
        env: Any = _EmptyEnv(),
        messages: Sequence[dict[str, Any]] = (),
        extra_args: dict[str, Any] | None = None,
    ) -> tuple[str, Any, Any, Sequence[dict[str, Any]], dict[str, Any]]:
        if not messages or messages[-1].get("role") != "tool":
            return query, runtime, env, messages, extra_args if extra_args is not None else {}

        session_id, call_args = _session_context(extra_args)

        # AgentDojo may execute several tool calls in one batch. Match its
        # detector behavior by screening all contiguous trailing tool results.
        for message in reversed(messages):
            if message.get("role") != "tool":
                break
            text = _message_text(message.get("content"))
            if not text:
                continue

            from gateway.text_normalizer import find_hidden_tag_text, normalize

            blocked, layer, _, _ = self._get_middleware()._run_injection_ensemble(
                normalize(text), session_id
            )
            if not blocked and find_hidden_tag_text(text):
                blocked, layer = True, "input_hygiene"

            with self._stats_lock:
                self._scanned_messages += 1
                if blocked:
                    self._blocked_messages += 1
                    layer_name = layer or "unknown"
                    self._blocked_layers[layer_name] = self._blocked_layers.get(layer_name, 0) + 1
            if blocked:
                message["content"] = _omit_text(message.get("content"))

        return query, runtime, env, messages, call_args

    def stats(self) -> dict[str, Any]:
        with self._stats_lock:
            return {
                "scanned_tool_messages": self._scanned_messages,
                "omitted_tool_messages": self._blocked_messages,
                "omissions_by_layer": dict(sorted(self._blocked_layers.items())),
            }
