"""The local web UI (git_assistant/webui.py + session.py), driven over HTTP with FastAPI's test
client. Every LLM call is mocked: no network, no budget. Grouped by the step's items:
  2. server: repos, inspected state, explanation, fixes with tiers
  3. approval: nothing above safe runs without a valid single-use, expiring code
  4. re-inspection: the page says whether the fix worked
(5. security is in test_webui_security.py)
"""

import json
import re
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from git_assistant import diagnose as dg
from git_assistant.session import Workspace, tuned_scenarios
from git_assistant.webui import create_app
from mcp_servers.git_local import execute as ex

CREDENTIAL = re.compile(r"gh[pousr]_\w{16,}|github_pat_\w{16,}|glpat-\w{16,}|AKIA[0-9A-Z]{16}|"
                        r"://[^/\s:@]+:[^/\s@]+@|-----BEGIN [A-Z ]*PRIVATE KEY")
SECRET = "s3cret-path"


@pytest.fixture
def ui(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    monkeypatch.setenv("GIT_TOOL_ALLOWED_ROOTS", str(root))
    monkeypatch.setattr(ex, "LOG", tmp_path / "exec.jsonl")
    monkeypatch.setattr(ex, "APPROVALS_LOG", tmp_path / "approvals.log")
    monkeypatch.setattr(ex, "_STEPS", {})
    ws = Workspace(root)
    client = TestClient(create_app(ws, SECRET, {"testserver"}))
    client.ws = ws
    return client


def build(c, scenario):
    r = c.post(f"/{SECRET}/build", data={"scenario": scenario})
    assert r.status_code == 200, r.text
    return str(r.url).rsplit("/", 1)[1]


def act(c, name, action, **data):
    r = c.post(f"/{SECRET}/repo/{name}/{action}", data=data)
    assert r.status_code == 200, r.text
    return r.text


def pending_id(html):
    m = re.search(r'name="step_id" value="(\w+)"', html)
    return m.group(1) if m else None


def code_for(step_id):
    return ex.human_view(step_id)["code"]


def fake_llm(monkeypatch, plan: dict, seen: list | None = None):
    """The assistant's LLM, mocked: returns `plan` as JSON and records what it was sent."""
    def chat(messages, **kw):
        if seen is not None:
            seen.append(json.dumps(messages))
        return json.dumps(plan)
    monkeypatch.setattr(dg, "chat", chat)

    def no_docs(*a, **k):
        raise ConnectionError("qdrant not running")
    monkeypatch.setattr(dg, "gather_docs", no_docs)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()


# ------------------------------------------------------------------ 2. the server

def test_index_lists_only_tuned_scenarios(ui):
    html = ui.get(f"/{SECRET}/").text
    assert "merge_conflict" in html and "push_rejected" in html
    assert "ho_" not in html                              # held-out scenarios never offered
    assert len(tuned_scenarios()) == 19


def test_repo_page_shows_problem_state_explanation_and_tiers(ui, monkeypatch):
    fake_llm(monkeypatch, {"cause": "Both branches changed the same line of app.py.",
                           "explanation": "Git stopped so you can choose which version to keep.",
                           "steps": [{"command": "", "why": "Edit app.py and remove the conflict markers"},
                                     {"command": "git add app.py", "why": "mark it resolved"},
                                     {"command": "git commit", "why": "finish the merge"}],
                           "warnings": ["git merge --abort goes back to before the merge"], "sources": []})
    name = build(ui, "merge_conflict")
    html = ui.get(f"/{SECRET}/repo/{name}").text
    assert "I tried to merge my feature branch" in html and "git merge feature" in html
    assert "a merge is in progress, 1 conflicted" in html          # the inspected state, in words
    html = act(ui, name, "diagnose", mode="llm")
    assert "Both branches changed the same line" in html and "choose which version" in html
    assert "git merge --abort goes back" in html
    assert re.search(r"<code>git add app.py</code> <span class=\"tier\"[^>]*>safe</span>", html)
    assert "documentation search was not reachable" in html       # docs down: said, not hidden
    assert "From: assistant" in html


def test_llm_failure_is_shown_not_a_crash(ui, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("429 from groq")
    monkeypatch.setattr(dg, "chat", broken)
    monkeypatch.setattr(dg, "gather_docs", lambda *a, **k: ([], ""))
    name = build(ui, "push_rejected")
    html = act(ui, name, "diagnose", mode="llm")
    assert "429 from groq" in html and "Ask the assistant" in html


def test_model_text_is_escaped(ui, monkeypatch):
    fake_llm(monkeypatch, {"cause": "<script>alert(1)</script>", "explanation": "<img src=x onerror=alert(1)>",
                           "steps": [{"command": "git status", "why": "<b>look</b>"}], "warnings": []})
    name = build(ui, "push_rejected")
    html = act(ui, name, "diagnose", mode="llm")
    assert "<script>" not in html and "<img" not in html and "&lt;script&gt;" in html


def test_page_guards(ui):
    name = build(ui, "push_rejected")
    assert ui.get("/").status_code == 404
    assert ui.get("/guess/").status_code == 404
    assert ui.get(f"/{SECRET}/", headers={"host": "evil.example"}).status_code == 404       # DNS rebinding
    r = ui.post(f"/{SECRET}/repo/{name}/diagnose", data={"mode": "key"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = ui.post(f"/{SECRET}/repo/{name}/diagnose", data={"mode": "key"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    r = ui.get(f"/{SECRET}/")
    assert "default-src 'none'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    assert "<script" not in r.text                                     # no JavaScript on the page at all
    assert ui.get(f"/{SECRET}/repo/..%2F..%2Fetc").status_code == 404
    assert ui.post(f"/{SECRET}/build", data={"scenario": "ho_pushed_secret"}).status_code == 200
    assert not any(n.startswith("ho_") for n in ui.ws.cases)          # held-out can't be built either


# ------------------------------------------------------------------ 3. approval

def test_safe_steps_run_with_one_click_careful_ones_need_the_code(ui):
    name = build(ui, "detached_head")
    act(ui, name, "diagnose", mode="key")                 # switch -c save-notes, switch main, merge
    html = act(ui, name, "run", step="0")
    assert pending_id(html) is None and "done" in html    # safe: ran, no code asked
    act(ui, name, "run", step="1")
    html = act(ui, name, "run", step="2")                 # merge: careful
    sid = pending_id(html)
    assert sid and "Waiting for your approval" in html
    assert code_for(sid) not in html                      # default: the code is only in the terminal
    assert code_for(sid) in ex.APPROVALS_LOG.read_text()  # ... and in logs/approvals.log
    head = git(ui.ws.get(name).work, "rev-parse", "HEAD")
    html = act(ui, name, "approve", step_id=sid, code="WRONG1")
    assert "wrong approval code" in html and pending_id(html) == sid
    assert git(ui.ws.get(name).work, "rev-parse", "HEAD") == head      # nothing ran
    act(ui, name, "approve", step_id=sid, code=code_for(sid))
    assert git(ui.ws.get(name).work, "rev-parse", "HEAD") != head


def test_three_wrong_codes_cancel_the_step(ui):
    name = build(ui, "push_rejected")
    act(ui, name, "diagnose", mode="key")
    sid = pending_id(act(ui, name, "run", step="0"))
    for _ in range(3):
        html = act(ui, name, "approve", step_id=sid, code="AAAAAA")
    assert "cancelled" in html and pending_id(html) is None
    assert ex.step_status(sid)["status"] == "cancelled"


def test_code_is_single_use_and_expires(ui, monkeypatch):
    name = build(ui, "push_rejected")
    act(ui, name, "diagnose", mode="key")
    sid = pending_id(act(ui, name, "run", step="0"))
    code = code_for(sid)
    act(ui, name, "approve", step_id=sid, code=code)
    assert ex.execute_with_code(sid, code)["ok"] is False                  # used: never again
    # expiry: a step approved after the token's lifetime is refused
    sid = pending_id(act(ui, name, "run", step="1"))
    code = code_for(sid)
    real = time.time
    monkeypatch.setattr(ex.time, "time", lambda: real() + ex.TOKEN_TTL_S + 1)
    html = act(ui, name, "approve", step_id=sid, code=code)
    assert "expired" in html
    remote = ui.ws.get(name).root / "origin.git"
    assert git(remote, "rev-parse", "main") == ui.ws.get(name).snap["origin_main"]   # nothing pushed


def test_run_safe_step_refuses_anything_above_safe(ui):
    name = build(ui, "push_rejected")
    work = ui.ws.get(name).work
    p = ex.prepare_for_model(str(work), "push", {"remote": "origin", "branch": "main"}, announce_safe=False)
    assert p["tier"] == "careful"
    r = ex.run_safe_step(p["step_id"])
    assert not r["ok"] and "needs the approval code" in r["refused"]


def test_approval_button_mode_carries_the_code(tmp_path, monkeypatch):
    """GIT_APPROVAL_UI=1: one click approves, because the button carries the step's code."""
    monkeypatch.setenv("GIT_TOOL_ALLOWED_ROOTS", str(tmp_path / "ws"))
    monkeypatch.setattr(ex, "LOG", tmp_path / "exec.jsonl")
    monkeypatch.setattr(ex, "APPROVALS_LOG", tmp_path / "approvals.log")
    ws = Workspace(tmp_path / "ws", approval_ui=True)
    c = TestClient(create_app(ws, SECRET, {"testserver"}))
    name = build(c, "push_rejected")
    act(c, name, "diagnose", mode="key")
    html = act(c, name, "run", step="0")
    sid = pending_id(html)
    assert f'name="code" value="{code_for(sid)}"' in html
    html = act(c, name, "approve", step_id=sid, code=code_for(sid))
    assert pending_id(html) is None


def test_approval_button_is_off_by_default():
    import inspect
    from git_assistant import webui
    assert 'os.environ.get("GIT_APPROVAL_UI", "0") == "1"' in inspect.getsource(webui.main)
    assert inspect.signature(Workspace).parameters["approval_ui"].default is False


# ------------------------------------------------------------------ 4. re-inspection

@pytest.mark.parametrize("scenario", ["push_rejected", "detached_head", "merge_conflict", "no_upstream"])
def test_answer_key_fix_runs_and_the_page_says_fixed(ui, scenario):
    name = build(ui, scenario)
    assert "Did it work?" not in ui.get(f"/{SECRET}/repo/{name}").text
    act(ui, name, "diagnose", mode="key")
    case = ui.ws.get(name)
    for i in range(len(case.plan["steps"])):
        html = act(ui, name, "run", step=str(i))
        while True:
            if case.manual:
                html = act(ui, name, "manual", simulate="1")
            elif case.pending:
                html = act(ui, name, "approve", step_id=case.pending["step_id"],
                           code=code_for(case.pending["step_id"]), phrase=case.pending["phrase"] or "")
            else:
                break
    assert "Fixed: the repo is in the state it should be." in html, html
    assert "all safety checks pass" in html
    assert case.result["now"] in html and "working tree clean" in case.result["now"]


def test_half_done_fix_says_not_fixed(ui):
    name = build(ui, "push_rejected")
    act(ui, name, "diagnose", mode="key")
    case = ui.ws.get(name)
    act(ui, name, "run", step="0")
    html = act(ui, name, "approve", step_id=case.pending["step_id"], code=code_for(case.pending["step_id"]))
    assert "Not fixed yet." in html                     # rebased, not pushed yet
    assert "0 behind" in case.result["now"]


def test_the_tool_cannot_reach_outside_the_workspace(ui, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    subprocess.run(["git", "init", "-q", str(outside)], check=True)
    assert ex.prepare(str(outside), "stash", {})["tier"] == "blocked"


def test_referrer_policy_lets_our_own_forms_post(ui):
    """With Referrer-Policy: no-referrer a browser sends "Origin: null" on the page's own form
    posts, and the cross-site guard then refuses every button (found when driving the page in
    Chromium). same-origin keeps the real Origin for our forms and sends nothing elsewhere."""
    from mcp_servers.git_local import approval_ui
    import inspect
    assert ui.get(f"/{SECRET}/").headers["referrer-policy"] == "same-origin"
    assert '"Referrer-Policy", "same-origin"' in inspect.getsource(approval_ui)
    name = build(ui, "push_rejected")
    r = ui.post(f"/{SECRET}/repo/{name}/diagnose", data={"mode": "key"}, headers={"origin": "null"})
    assert r.status_code == 403                    # an opaque origin is still refused


def test_demo_is_one_command(tmp_path, monkeypatch, capsys):
    """`python -m git_assistant.webui --demo` builds the broken repo, limits the tool to the
    workspace, serves on 127.0.0.1 only and prints the address in the terminal."""
    import os
    import uvicorn
    from git_assistant import webui
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    monkeypatch.setattr(webui.webbrowser, "open", lambda url: seen.update(opened=url))
    monkeypatch.setattr(webui.threading, "Timer", lambda delay, f, args: type("T", (), {"start": lambda s: f(*args)})())
    monkeypatch.setattr(ex, "LOG", tmp_path / "exec.jsonl")
    monkeypatch.setenv("GIT_TOOL_ALLOWED_ROOTS", "/somewhere/else")
    webui.main(["--demo", "--workspace", str(tmp_path / "ws"), "--port", "8799"])
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8799
    assert os.environ["GIT_TOOL_ALLOWED_ROOTS"] == str((tmp_path / "ws").resolve())
    assert (tmp_path / "ws" / "merge_conflict-1" / "work" / ".git").exists()
    err = capsys.readouterr().err
    assert "http://127.0.0.1:8799/" in err and seen["opened"] in err and "/repo/merge_conflict-1" in seen["opened"]


def test_steps_the_tool_does_not_run_are_never_marked_done(ui, monkeypatch):
    fake_llm(monkeypatch, {"cause": "x", "explanation": "y", "warnings": [],
                           "steps": [{"command": "git checkout -- app.py", "why": "discard the edit"},
                                     {"command": "ssh-keygen -t ed25519", "why": "make a key"},
                                     {"command": "git merge", "why": "no branch named"},
                                     {"command": "git status", "why": "look"}]})
    name = build(ui, "push_rejected")
    act(ui, name, "diagnose", mode="llm")
    case = ui.ws.get(name)
    for i in range(4):
        html = act(ui, name, "run", step=str(i))
    assert case.done == {3}                                   # only the read-only look counts as done
    assert set(case.skipped) == {0, 1, 2}
    assert "not run by the tool: discarding file changes has no tool operation" in html


def test_a_new_plan_cancels_the_step_left_waiting(ui):
    name = build(ui, "push_rejected")
    act(ui, name, "diagnose", mode="key")
    sid = pending_id(act(ui, name, "run", step="0"))
    code = code_for(sid)
    html = act(ui, name, "diagnose", mode="key")
    assert pending_id(html) is None and ex.step_status(sid)["status"] == "rejected"
    assert not ex.execute_with_code(sid, code)["ok"]          # the old code is dead


def test_an_llm_error_costs_one_call_not_two(ui, monkeypatch):
    calls = []
    def broken(*a, **k):
        calls.append(1)
        raise RuntimeError("400 bad request")
    monkeypatch.setattr(dg, "chat", broken)
    def no_docs(*a, **k):
        raise ConnectionError("qdrant not running")
    monkeypatch.setattr(dg, "gather_docs", no_docs)
    name = build(ui, "push_rejected")
    html = act(ui, name, "diagnose", mode="llm")
    assert "400 bad request" in html and len(calls) == 1
