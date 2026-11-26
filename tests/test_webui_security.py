"""Security of the web UI (item 5): with a mocked LLM that proposes the worst commands, nothing
destructive runs without the code AND the typed phrase; no secret reaches the page or the LLM;
the LLM never sees an approval code; and git fsck plus every safety invariant hold after full
fixes. (Page guards: wrong secret, foreign Host, cross-site POST are in test_webui.py.)
"""

import pytest

from tests.test_webui import CREDENTIAL, SECRET, act, build, code_for, fake_llm, git, pending_id, ui  # noqa: F401


def test_force_push_and_reset_hard_never_run_without_code_and_phrase(ui, monkeypatch):
    fake_llm(monkeypatch, {"cause": "x", "explanation": "y", "warnings": [],
                           "steps": [{"command": "git push --force origin main", "why": "overwrite"},
                                     {"command": "git reset --hard HEAD~1", "why": "drop it"}]})
    name = build(ui, "push_rejected")
    case = ui.ws.get(name)
    remote, work = case.root / "origin.git", case.work
    before = (git(remote, "rev-parse", "main"), git(work, "rev-parse", "HEAD"))
    html = act(ui, name, "diagnose", mode="llm")
    assert 'destructive</span>' in html                  # the plan shows the real tier
    for i in (0, 1):
        html = act(ui, name, "run", step=str(i))
        sid = pending_id(html)
        assert sid and "destructive" in html and "Type <code>" in html
        html = act(ui, name, "approve", step_id=sid, code=code_for(sid), phrase="yes")   # wrong phrase
        assert "type exactly" in html and pending_id(html) == sid                         # code not used up
        html = act(ui, name, "approve", step_id=sid, code="", phrase=case.pending["phrase"])  # no code
        assert "Type the approval code first" in html and pending_id(html) == sid
        assert (git(remote, "rev-parse", "main"), git(work, "rev-parse", "HEAD")) == before
        act(ui, name, "reject", step_id=sid)
    assert (git(remote, "rev-parse", "main"), git(work, "rev-parse", "HEAD")) == before
    assert all(e["result"] != "ok" for e in case.ran)


def test_destructive_with_code_and_phrase_makes_a_backup_first(ui, monkeypatch):
    fake_llm(monkeypatch, {"cause": "x", "explanation": "y", "warnings": [],
                           "steps": [{"command": "git reset --hard HEAD", "why": "throw away the edit"}]})
    name = build(ui, "uncommitted_on_wrong_branch")
    case = ui.ws.get(name)
    act(ui, name, "diagnose", mode="llm")
    sid = pending_id(act(ui, name, "run", step="0"))
    act(ui, name, "approve", step_id=sid, code=code_for(sid), phrase=case.pending["phrase"])
    ran = [e for e in case.ran if e.get("op") == "reset_hard"][0]
    assert ran["result"] == "ok" and ran["backups"] and ran["confirmed"]
    assert next(c for c in case.result["safety"] if c["check"].startswith("no data loss"))["ok"]


@pytest.mark.parametrize("scenario", ["committed_secret", "push_protection_secret", "auth_bad_token"])
def test_no_secret_reaches_the_page_or_the_llm(ui, monkeypatch, scenario):
    seen = []
    fake_llm(monkeypatch, {"cause": "a token was committed", "explanation": "rotate it", "warnings": [],
                           "steps": [{"command": "git log -p", "why": "look"}]}, seen)
    name = build(ui, scenario)
    if scenario == "push_protection_secret":            # not vacuous: the repo really holds a token
        assert CREDENTIAL.search(git(ui.ws.get(name).work, "log", "-p", "--all"))
    html = act(ui, name, "diagnose", mode="llm")
    assert not CREDENTIAL.search(html), CREDENTIAL.search(html)
    html = act(ui, name, "diagnose", mode="key")                  # the answer-key plan too
    assert not CREDENTIAL.search(html)
    assert seen and not any(CREDENTIAL.search(s) for s in seen)
    assert SECRET not in "".join(seen)                              # nor the page address


def test_the_llm_never_sees_an_approval_code(ui, monkeypatch):
    seen = []
    fake_llm(monkeypatch, {"cause": "x", "explanation": "y", "warnings": [],
                           "steps": [{"command": "git push origin main", "why": "publish"}]}, seen)
    name = build(ui, "no_upstream")
    act(ui, name, "diagnose", mode="llm")
    sid = pending_id(act(ui, name, "run", step="0"))
    code = code_for(sid)
    act(ui, name, "diagnose", mode="llm")                              # plan again while a code exists
    assert not any(code in s for s in seen)


@pytest.mark.parametrize("scenario", ["merge_conflict", "wrong_branch_commit", "rebase_conflict", "committed_secret"])
def test_git_fsck_clean_after_a_full_fix(ui, scenario):
    name = build(ui, scenario)
    act(ui, name, "diagnose", mode="key")
    case = ui.ws.get(name)
    for i in range(len(case.plan["steps"])):
        act(ui, name, "run", step=str(i))
        while case.pending or case.manual:
            if case.manual:
                act(ui, name, "manual", simulate="1")
            else:
                act(ui, name, "approve", step_id=case.pending["step_id"], code=code_for(case.pending["step_id"]),
                    phrase=case.pending["phrase"] or "")
    checks = {c["check"]: c for c in case.result["safety"]}
    fsck = next(c for n, c in checks.items() if "fsck" in n)
    assert fsck["ok"], fsck
    assert all(c["ok"] for c in case.result["safety"]), case.result["safety"]
