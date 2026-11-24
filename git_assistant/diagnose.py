"""Diagnose a beginner's git problem: look at the repo, read the docs, propose a fix.

Pipeline (fixed, no tool-calling loop yet, so runs are comparable):
  1. inspect   repo_state + log (+ reflog if detached, + conflict diff if merging)
  2. retrieve  git_docs passages for the error text and the user's words
  3. answer    LLM returns JSON: cause, explanation, steps [{command, why}], warnings
  4. rate      every proposed command gets a risk tier, using the repo state (risk.in_context)
  5. review    if a step is destructive, ask once for a safer plan (like the groundedness
               checker: the model proposes, a deterministic tool checks, the model revises)

The read functions are the same ones the MCP server exposes; here they are called in-process
so the benchmark measures the reasoning, not the transport.
"""

import json
import re

from common.llm import chat
from mcp_servers.git_local import inspect_repo as ir
from mcp_servers.git_local.risk import after, in_context

SYSTEM = """You help a beginner who is stuck with git. Be calm, concrete and safe.

You get: what the user said, the command that failed and its output, the state of their repo
(read by a tool, trust it), and documentation excerpts numbered [1], [2]...

Reply with JSON only:
{"cause": "one or two plain sentences: what is actually wrong",
 "explanation": "short beginner-friendly explanation of why git does this",
 "steps": [{"command": "exact git command, or empty if the step is manual", "why": "what it does"}],
 "warnings": ["risks the user must know before running the steps"],
 "sources": [numbers of the excerpts you used]}

Rules:
- Use the real branch, remote and file names from the repo state.
- Prefer the least risky fix that solves the problem. Say how to undo or abort when possible.
- Give the complete sequence, until the repo is in the state the user wants (for example:
  the work is on the right branch AND removed from the wrong one; a saved commit is merged
  back where it belongs). Do not stop halfway.
- Put manual steps (like editing a file) as a step with an empty command.
- Never rewrite history that is already on a shared remote (no force-push, no reset of pushed
  commits) unless there is no other way, and then warn clearly.
- If a secret was committed, say whether it was pushed; if it was, say it must be rotated.
- Never put a password or token inside a command or a URL.
- Use only what the repo state, the error and the excerpts support. Do not invent output."""


def compact(state: dict) -> dict:
    """Shorter for the prompt: file lists without the count wrappers, folders as '.'
    (the model doesn't need this machine's paths), empty fields dropped."""
    out = {}
    for k, v in state.items():
        if k in ("asked", "root"):
            continue
        if isinstance(v, dict) and "items" in v:
            v = v["items"] + (["..."] if v["truncated"] else [])
        if v not in (None, [], {}, 0, False) or k in ("branch", "detached"):
            out[k] = v
    return out


def gather_state(path: str) -> tuple[dict, str]:
    state = ir.repo_state(path)
    if not state.get("is_repo"):
        return state, json.dumps(state)
    parts = ["repo_state:", json.dumps(compact(state)), "\ngit log:", ir.log(path, 10)]
    if state["detached"]:
        parts += ["\ngit reflog:", ir.reflog(path, 8)]
    if state["in_progress"] == "merge" and state["conflicted"]["count"]:
        parts += ["\nconflict diff:", ir.diff(path)[:1500]]
    return state, "\n".join(parts)


def gather_docs(query: str, k: int = 4) -> tuple[list, str]:
    from knowledge.retrieve import search  # imported lazily: --no-docs runs need no Qdrant
    hits = search(query, k=k, collection="git_docs", mode="hybrid")
    text = "\n\n".join(f"[{i}] ({h.source}: {h.path} > {h.section})\n{h.text[:1200]}"
                       for i, h in enumerate(hits, 1))
    return hits, text


def parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    steps = [s for s in data.get("steps", []) if isinstance(s, dict)]
    return {"cause": str(data.get("cause", "")), "explanation": str(data.get("explanation", "")),
            "steps": [{"command": str(s.get("command", "")).strip(), "why": str(s.get("why", ""))} for s in steps],
            "warnings": [str(w) for w in data.get("warnings", [])],
            "sources": data.get("sources", []), "parsed": bool(data)}


def diagnose(path: str, user_says: str, command: str, error_output: str, task_id: str,
             use_state: bool = True, use_docs: bool = True, safety_retry: bool = True) -> dict:
    state, state_text = gather_state(path) if use_state else (None, "(repo state not available)")
    hits, docs_text = [], "(no documentation)"
    if use_docs:
        first_error = next((l for l in error_output.splitlines() if re.search(r"error|fatal|reject|conflict|detached", l, re.I)), "")
        hits, docs_text = gather_docs(f"{user_says} {command} {first_error}")
    user = (f"User says: {user_says}\n\nCommand: {command}\nOutput:\n{error_output}\n\n"
            f"Repo:\n{state_text}\n\nDocumentation:\n{docs_text}")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    raw = chat(messages, task_id=task_id, agent="git_assistant", temperature=0.2, json_mode=True)
    out = rate(parse(raw), state)
    if not out["parsed"]:
        out["raw"] = raw[:2000]  # keep it so a failed parse can be debugged from the results file

    # Safety review: the risk tool, not the model, decides what is destructive. If the plan
    # has a destructive step, ask once for a safer plan; keep the step only with a warning.
    out["safety_retry"] = False
    flagged = [s for s in out["steps"] if s.get("tier") == "destructive"]
    if safety_retry and flagged:
        review = "\n".join(f"- `{s['command']}`: {s['tier_reason']}" for s in flagged)
        messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": (
            "The risk checker rated these steps DESTRUCTIVE in this repo:\n" + review +
            "\nIf a safer sequence solves the problem, replace them. If a step is truly needed, "
            "keep it and add a warning that says exactly what it can lose. Same JSON format.")}]
        raw2 = chat(messages, task_id=task_id, agent="git_assistant", temperature=0.2, json_mode=True)
        second = rate(parse(raw2), state)
        if second["parsed"]:
            out = second | {"safety_retry": True, "first_plan": [s["command"] for s in out["steps"]]}
    out["docs"] = [f"{h.source}:{h.path}" for h in hits]
    return out


def rate(out: dict, state: dict | None) -> dict:
    """Risk tier for every step, each in the state left by the steps before it."""
    current = state
    for s in out["steps"]:
        if s["command"]:
            r = in_context(s["command"], current)
            s.update(tier=r["tier"], tier_reason=r["reason"])
            current = after(s["command"], current)
    return out
