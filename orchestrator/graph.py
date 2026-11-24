"""Orchestrator loop: decide -> (call_tool -> decide)* -> answer.

    decide     the model reads the conversation and returns JSON: call a tool, or answer.
    call_tool  runs a GitHub MCP tool, turns the results into Evidence, feeds them back.
    answer     returns the final text. If a tool failed, it reports the error WITHOUT asking
               the model, so the model can never invent data to cover a failure.
"""

import json

from langgraph.graph import END, StateGraph

from common.llm import chat
from common.schemas import Evidence
from common.tracing import get_trace
from orchestrator.mcp_client import ToolError
from orchestrator.state import OrchestratorState

MAX_STEPS = 8    # README 3.1: step limit
MAX_RETRIES = 2  # README 3.1: retry limit (bad model JSON, or tool connection errors)
MAX_RESULT_CHARS = 6000  # CI logs can be huge; cap what goes back into the prompt

# Explicit allowlist: even if the server exposes more, the model may only use these.
# All are read-only (the server also runs with GITHUB_READ_ONLY=1 as a second guard).
ALLOWED_TOOLS = {
    "list_issues",        # open/closed issues
    "list_commits",       # commit history
    "list_pull_requests", # open/closed PRs
    "pull_request_read",  # one PR: details incl. mergeable / merge conflicts, diff, checks
    "actions_list",       # CI workflow runs (find failures)
    "get_job_logs",       # CI error logs
}

SYSTEM_PROMPT = """You are a GitHub assistant with these read-only tools:
{catalog}
{repo_hint}
Reply with ONLY one JSON object, no other text:
- to use a tool: {{"action": "<tool name>", "args": {{...}}}}
- to answer:     {{"action": "answer", "answer": "..."}}
Use several tools in sequence if needed (e.g. list_pull_requests, then pull_request_read
to check if a PR has merge conflicts). Only state facts that appear in tool results.
Never invent data. Cite URLs when the results contain them."""


def system_message(catalog: str, repo: str | None) -> dict:
    hint = f'The user\'s repository is "{repo}" (owner/repo).' if repo else ""
    return {"role": "system", "content": SYSTEM_PROMPT.format(catalog=catalog, repo_hint=hint)}


def _parse_decision(text: str) -> dict:
    # Models sometimes wrap JSON in ```json fences; strip them before parsing.
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    decision = json.loads(text)
    action = decision.get("action")
    if action != "answer" and action not in ALLOWED_TOOLS:
        raise ValueError(f"tool {action!r} is not allowed")
    return decision


def _items(result) -> list:
    """Find the list of records in a result: a bare list, or the first list inside a dict
    ({"issues": [...]}, {"workflow_runs": [...]}, ...). A single record becomes [record]."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for v in result.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        return [result]
    return []


def result_to_evidence(tool: str, result) -> list[Evidence]:
    """One Evidence per record that has a URL. Field names differ per tool, so try the usual ones."""
    evidence = []
    for r in _items(result):
        if not isinstance(r, dict):
            continue
        url = r.get("html_url") or r.get("url")
        if not url:
            continue
        label = (r.get("title") or (r.get("commit") or {}).get("message")
                 or r.get("name") or r.get("display_title") or "")
        ref = (f"#{r['number']}" if r.get("number") else (r.get("sha") or "")[:7] or r.get("id") or "")
        extra = [f"{k}={r[k]}" for k in ("state", "mergeable", "mergeable_state", "conclusion") if k in r]
        text = f"{ref} {label.splitlines()[0] if label else ''} {' '.join(extra)}".strip()
        evidence.append(Evidence(text=text[:300], source=url, tool=f"github.{tool}"))
    return evidence


def build_graph(tools):
    """`tools` is anything with `async call(name, args)`: the real MCP wrapper or a test fake."""

    def decide(state: OrchestratorState) -> dict:
        step = state["step_count"] + 1
        if step > MAX_STEPS:
            return {"step_count": step, "pending_tool": None,
                    "final_answer": f"Stopped: reached the {MAX_STEPS}-step limit without an answer."}
        last_error = None
        for _ in range(1 + MAX_RETRIES):
            reply = chat(state["messages"], task_id=state["task_id"], agent="orchestrator")
            try:
                decision = _parse_decision(reply)
                break
            except (ValueError, json.JSONDecodeError) as e:
                last_error = e
        else:
            return {"step_count": step, "pending_tool": None,
                    "final_answer": f"Stopped: the model gave invalid output {1 + MAX_RETRIES} times ({last_error})."}

        msg = {"role": "assistant", "content": reply}
        if decision["action"] == "answer":
            return {"messages": [msg], "step_count": step, "pending_tool": None,
                    "final_answer": decision.get("answer", "")}
        return {"messages": [msg], "step_count": step,
                "pending_tool": {"name": decision["action"], "args": decision.get("args", {})}}

    async def call_tool(state: OrchestratorState) -> dict:
        name, args = state["pending_tool"]["name"], state["pending_tool"]["args"]
        trace = get_trace(state["task_id"])
        span = trace.span(name=f"tool.{name}", input=args) if trace else None
        update = {"step_count": state["step_count"] + 1, "pending_tool": None}
        try:
            for attempt in range(1 + MAX_RETRIES):
                try:
                    result = await tools.call(name, args)
                    break
                except ToolError:
                    raise  # GitHub said no (bad repo/token/args): retrying won't help
                except Exception:
                    if attempt == MAX_RETRIES:
                        raise  # connection problem that persisted through the retries
        except Exception as e:
            error = f"{name} -> {type(e).__name__}: {e}"
            if span:
                span.end(output=error, level="ERROR")
            return {**update, "tool_error": error}

        evidence = result_to_evidence(name, result)
        if span:
            span.end(output=result)
        raw = json.dumps(result, default=str) if not isinstance(result, str) else result
        if len(raw) > MAX_RESULT_CHARS:
            raw = raw[:MAX_RESULT_CHARS] + " ...[truncated]"
        tool_msg = {"role": "user", "content": f"Tool {name} result:\n{raw}"}
        return {**update, "messages": [tool_msg], "evidence": evidence}

    def answer(state: OrchestratorState) -> dict:
        if state.get("tool_error"):
            return {"final_answer": f"The GitHub tool failed, so I can't answer from real data. "
                                    f"Reason: {state['tool_error']}"}
        return {}  # final_answer was already set by decide

    def route_after_decide(state: OrchestratorState) -> str:
        return "call_tool" if state.get("pending_tool") is not None else "answer"

    def route_after_tool(state: OrchestratorState) -> str:
        return "answer" if state.get("tool_error") else "decide"

    g = StateGraph(OrchestratorState)
    g.add_node("decide", decide)
    g.add_node("call_tool", call_tool)
    g.add_node("answer", answer)
    g.set_entry_point("decide")
    g.add_conditional_edges("decide", route_after_decide, ["call_tool", "answer"])
    g.add_conditional_edges("call_tool", route_after_tool, ["decide", "answer"])
    g.add_edge("answer", END)
    return g.compile()


def initial_state(question: str, task_id: str, catalog: str, repo: str | None = None) -> OrchestratorState:
    return {
        "messages": [system_message(catalog, repo), {"role": "user", "content": question}],
        "task_id": task_id, "evidence": [], "step_count": 0,
        "pending_tool": None, "tool_error": None, "final_answer": None,
    }
