"""The smoke run drives the web UI over its HTTP routes from build to recheck. Here it runs with the
answer key or a mocked LLM (no network); on a PC with a key it runs with the real model."""

import json
import os

import pytest

from git_assistant import diagnose as dg
from git_assistant import webui_smoke as sm
from mcp_servers.git_local import execute as ex


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    # smoke() points these at its own temp folder; monkeypatch puts the originals back afterwards
    monkeypatch.setattr(ex, "LOG", tmp_path / "exec.jsonl")
    monkeypatch.setattr(ex, "APPROVALS_LOG", tmp_path / "approvals.log")
    monkeypatch.setattr(ex, "_STEPS", {})
    monkeypatch.setenv("GIT_TOOL_ALLOWED_ROOTS", str(tmp_path / "unused"))


def no_docs(monkeypatch):
    def fail(*a, **k):
        raise ConnectionError("qdrant not running")
    monkeypatch.setattr(dg, "gather_docs", fail)


def test_answer_key_run_approves_careful_steps_and_ends_fixed(tmp_path):
    out = tmp_path / "pages"
    s = sm.smoke("push_rejected", out, use_llm=False)
    assert s["plan_source"].startswith("answer key") and s["fixed"] is True and s["safe"] is True
    assert s["actions"].count("approve") == 2            # the rebase and the push are careful
    assert all(r["result"] == "ok" for r in s["ran"])
    assert s["pages"][0] == "01_problem.html" and s["pages"][-1].endswith("_recheck.html")
    assert all((out / p).is_file() for p in s["pages"])
    assert "Fixed" in (out / s["pages"][-1]).read_text(encoding="utf-8")


def test_destructive_step_gets_the_phrase_and_a_backup(tmp_path):
    s = sm.smoke("wrong_branch_commit", tmp_path / "pages", use_llm=False)
    assert ("git reset --hard HEAD~1", "destructive", "ok") in [(r["command"], r["tier"], r["result"]) for r in s["ran"]]
    rows = [json.loads(l) for l in ex.LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(r.get("op") == "reset_hard" and r.get("backups") for r in rows), rows
    assert s["fixed"] is True and s["safe"] is True


def test_llm_plan_goes_through_the_same_routes(tmp_path, monkeypatch):
    no_docs(monkeypatch)
    plan = {"cause": "Both branches changed the same line.", "explanation": "Pick the lines to keep, then commit.",
            "steps": [{"command": "git add app.py", "why": "Marks the conflict as resolved once you edited it."},
                      {"command": "git commit --no-edit", "why": "Finishes the merge with the default message."}],
            "warnings": []}
    monkeypatch.setattr(dg, "chat", lambda messages, **kw: json.dumps(plan))
    s = sm.smoke("merge_conflict", tmp_path / "pages", use_llm=True)
    assert s["plan_source"] == "assistant"
    assert [p["command"] for p in s["plan"]] == ["git add app.py", "git commit --no-edit"]
    assert s["note"] and "documentation search" in s["note"]
    assert s["fixed"] is False and s["failed_checks"]     # the plan never resolved the markers: reported, not hidden


def test_llm_failure_is_reported_and_main_exits_non_zero(tmp_path, monkeypatch, capsys):
    no_docs(monkeypatch)

    def down(*a, **k):
        raise ConnectionError("groq unreachable")
    monkeypatch.setattr(dg, "chat", down)
    with pytest.raises(SystemExit, match="no plan"):
        sm.main(["--scenario", "merge_conflict", "--out", str(tmp_path / "pages")])
    summary = json.loads((tmp_path / "pages" / "summary.json").read_text(encoding="utf-8"))
    assert summary["plan"] is None and "groq unreachable" in summary["error"]


def test_held_out_scenarios_are_refused(tmp_path):
    with pytest.raises(SystemExit, match="held-out"):
        sm.main(["--answer-key", "--scenario", "ho_merge_conflict_two_files", "--out", str(tmp_path)])


def test_env_file_is_read_without_overriding_or_printing(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("SMOKE_TEST_KEY=sk-not-a-real-key-123\nSMOKE_TEST_SET=from-file\n", encoding="utf-8")
    monkeypatch.setenv("SMOKE_TEST_SET", "already-set")
    monkeypatch.delenv("SMOKE_TEST_KEY", raising=False)
    assert sm.load_env_file(str(env)) == 1
    assert os.environ["SMOKE_TEST_KEY"] == "sk-not-a-real-key-123"
    assert os.environ["SMOKE_TEST_SET"] == "already-set"
    monkeypatch.delenv("SMOKE_TEST_KEY")
    sm.main(["--answer-key", "--env-file", str(env), "--out", str(tmp_path / "pages")])
    printed = capsys.readouterr()
    assert "sk-not-a-real-key" not in printed.out + printed.err
