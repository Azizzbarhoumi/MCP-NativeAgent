"""Drive the web UI once, end to end, with the real LLM, and save every page it shows.

  python -m git_assistant.webui_smoke --scenario merge_conflict --out logs/webui_smoke
  python -m git_assistant.webui_smoke --answer-key            no LLM: the scenario's answer key

It goes through the same HTTP routes as a browser (FastAPI's test client, so no port is opened):
build the scenario repo, ask for a plan, run every step, approve what needs approval, recheck.
It plays the human, so it reads each approval code from the human's channel (what the terminal
would show) and types it in. That is only allowed here because the repo is a throwaway scenario
repo in a temp folder: the approval policy is "approve every step", like the benchmark.

Pages are saved as 01_problem.html, 02_plan.html, ... in --out, with a summary.json. The exit
code is 0 when every page rendered; whether the model's plan fixed the repo is reported, not
treated as a failure (that is what the benchmark measures).

--env-file PATH reads API keys from another folder's .env (for a git worktree that has none). The
file is only read, its values go into this process's environment and are never printed.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

MAX_ACTIONS = 40          # a plan never needs this many clicks; stops a loop if the UI misbehaves


def load_env_file(path: str) -> int:
    """Put the file's KEY=VALUE pairs into os.environ (variables already set win). Returns how
    many were added; the values are never printed."""
    from dotenv import dotenv_values           # installed with pydantic-settings
    added = 0
    for k, v in dotenv_values(path).items():
        if v is not None and k not in os.environ:
            os.environ[k] = v
            added += 1
    return added


def pending_step(html: str) -> str | None:
    m = re.search(r'name="step_id" value="(\w+)"', html)
    return m.group(1) if m else None


def smoke(scenario: str, out: Path, use_llm: bool) -> dict:
    root = Path(tempfile.mkdtemp(prefix="git-assistant-smoke-"))
    os.environ["GIT_TOOL_ALLOWED_ROOTS"] = str(root)
    from fastapi.testclient import TestClient

    from common import llm
    from git_assistant.session import Workspace
    from git_assistant.webui import create_app
    from mcp_servers.git_local import execute as ex
    ex.LOG = root / "git_exec.jsonl"                 # keep the run's logs with its throwaway repo
    ex.APPROVALS_LOG = root / "approvals.log"

    secret = "smoke"
    ws = Workspace(root / "ws")
    c = TestClient(create_app(ws, secret, {"testserver"}))
    out.mkdir(parents=True, exist_ok=True)
    pages, actions = [], []

    def save(label: str, r) -> str:
        if r.status_code != 200:
            raise RuntimeError(f"{label}: HTTP {r.status_code}: {r.text[:200]}")
        name = f"{len(pages) + 1:02d}_{label}.html"
        (out / name).write_text(r.text, encoding="utf-8")
        pages.append(name)
        return r.text

    def post(action: str, **data) -> str:
        actions.append(action + (f" {data['step']}" if "step" in data else ""))
        return save(action + (f"_step{int(data['step']) + 1}" if "step" in data else ""),
                    c.post(f"/{secret}/repo/{name}/{action}", data=data))

    r = c.post(f"/{secret}/build", data={"scenario": scenario})
    name = str(r.url).rsplit("/", 1)[1]
    save("problem", r)
    html = post("diagnose", mode="llm" if use_llm else "key")
    case = ws.get(name)
    if case.plan is None:                             # the LLM call failed: the page says why
        import html as htmllib
        shown = re.findall(r'<div class="flash (?:error|warn)">(.*?)</div>', html, re.S)
        return {"scenario": scenario, "plan": None, "pages": pages,
                "error": " | ".join(htmllib.unescape(m) for m in shown) or "no plan and no message on the page"}

    for i in range(len(case.plan["steps"])):
        html = post("run", step=str(i))
        while len(actions) < MAX_ACTIONS:
            if case.pending:
                view = ex.human_view(case.pending["step_id"])      # what the human's terminal shows
                html = post("approve", step_id=case.pending["step_id"], code=view["code"],
                            **({"phrase": case.pending["phrase"]} if case.pending["phrase"] else {}))
            elif case.manual:
                html = post("manual", simulate="1" if case.manual["can_simulate"] else "0")
            else:
                break
    html = post("recheck")
    res = case.result or {}
    usage = llm.USAGE.get(case.task_id, [])
    return {
        "scenario": scenario, "plan_source": case.plan_source,
        "models": sorted({f"{p}/{m}" for _a, p, m, _i, _o in usage}),
        "llm_calls": len(usage), "tokens": llm.TOKENS.get(case.task_id, 0),
        "plan": [{"command": s["command"], "tier": s.get("tier"), "why": s.get("why", "")} for s in case.plan["steps"]],
        "cause": case.plan.get("cause", ""), "note": case.plan.get("note"),
        "ran": [{"step": e["step"], "command": e["command"], "tier": e.get("tier"), "result": e.get("result")}
                for e in case.ran],
        "skipped": {str(k + 1): v for k, v in case.skipped.items()},
        "fixed": res.get("fixed"), "safe": res.get("safe"),
        "failed_checks": [ch["check"] for ch in res.get("state", []) + res.get("safety", []) if not ch["ok"]],
        "actions": actions, "pages": pages,
    }


def main(argv=None) -> None:
    from common.console import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="merge_conflict")
    ap.add_argument("--out", default="logs/webui_smoke")
    ap.add_argument("--answer-key", action="store_true", help="no LLM: plan = the scenario's answer key")
    ap.add_argument("--env-file", help="read API keys from this .env (only read, never printed)")
    a = ap.parse_args(argv)
    if a.env_file:
        print(f"read {load_env_file(a.env_file)} setting(s) from {a.env_file}", file=sys.stderr)
    from git_assistant.session import tuned_scenarios
    if a.scenario not in tuned_scenarios():
        raise SystemExit(f"unknown or held-out scenario {a.scenario!r}; the UI offers: {', '.join(tuned_scenarios())}")
    from common.config import settings
    if not a.answer_key:
        print(f"answer model: {settings.llm_provider}/{settings.llm_model}", file=sys.stderr)
    out = Path(a.out)
    summary = smoke(a.scenario, out, use_llm=not a.answer_key)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {len(summary['pages'])} page(s) and summary.json to {out}", file=sys.stderr)
    if summary.get("plan") is None:
        raise SystemExit("no plan: " + summary.get("error", "")[:300])


if __name__ == "__main__":
    main()
