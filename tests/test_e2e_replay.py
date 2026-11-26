"""Replay runs recorded plans again on today's harness with no LLM call. It must reuse the judge's
recorded verdicts, say when a recorded failure passes now (a harness bug since fixed), and say
when a recorded pass fails now (a harness or grader change broke something)."""

import argparse
import json
import sys

import pytest

from common import llm
from eval import run_git_e2e as e2e
from eval.git_scenarios.scenarios import SCENARIOS
from mcp_servers.git_local import execute

ARGS = argparse.Namespace(oracle=False, no_judge=False, dangerous="approve", replay="x")
BY_NAME = {b.__name__: b for b in SCENARIOS}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(e2e, "BUILD", tmp_path / "e2e")
    monkeypatch.setattr(e2e, "EVAL", tmp_path)
    (tmp_path / "results").mkdir()
    monkeypatch.setenv("GIT_TOOL_ALLOWED_ROOTS", str(tmp_path / "e2e"))
    monkeypatch.setattr(execute, "LOG", tmp_path / "exec.jsonl")

    def no_llm(*a, **kw):
        raise AssertionError("replay must not call an LLM")

    import git_assistant.diagnose as dg
    monkeypatch.setattr(llm, "chat", no_llm)
    monkeypatch.setattr(dg, "chat", no_llm)
    return tmp_path


def recorded(sid, commands, passed, diagnosis="correct", llm_ok=True):
    """A row as a real assistant run would have written it (only the fields replay reads)."""
    rub = [{"id": it["id"], "type": "llm", "ok": llm_ok, "why": "recorded"}
           for it in e2e.rubric_mod.load(sid)["items"] + e2e.BEGINNER["items"] if it["type"] == "llm"]
    return {"id": sid, "run": 0, "held_out": sid.startswith("ho_"), "passed": passed, "oracle": False,
            "diagnosis": diagnosis, "judge_reason": "recorded reason", "rubric": rub, "beginner": [],
            "assistant_tokens": 1234, "judge_tokens": 567, "usd_estimate": 0.001,
            "failure": None if passed else {"who": "assistant", "why": "repo not fixed: recorded"},
            "plan": commands,
            "answer": {"cause": "The remote has commits you don't have.", "explanation": "Fetch and replay yours on top.",
                       "steps": [{"command": c, "why": "This brings your branch up to date before you push again."}
                                 for c in commands], "warnings": [], "parsed": True}}


def test_bare_rebase_failure_from_the_benchmark_branch_is_relabelled_harness(harness):
    """On overnight/2026-11-26 a bare `git rebase` translated to nothing, so this plan failed there
    and was blamed on the assistant. Today's translator rebases onto the upstream."""
    rec = recorded("push_rejected", ["git fetch origin", "git rebase", "git push origin main"], passed=False)
    row = e2e.run_one(BY_NAME["push_rejected"], 0, ARGS, "t", recorded=rec)
    assert row["passed"], (row["failure"], row["steps"])
    assert e2e.replay_change(rec, row).startswith("harness:")
    assert row["diagnosis"] == "correct" and row["judge_reason"] == "recorded reason"
    assert row["assistant_tokens"] == 1234 and row["judge_tokens"] == 567     # cost of the recorded run


def test_recorded_pass_that_fails_now_is_a_regression(harness):
    rec = recorded("push_rejected", ["git status"], passed=True)
    row = e2e.run_one(BY_NAME["push_rejected"], 0, ARGS, "t", recorded=rec)
    assert not row["passed"]
    assert e2e.replay_change(rec, row).startswith("regression:")


def test_recorded_judge_verdicts_are_reused_for_advice_scenarios(harness):
    """auth scenarios are graded on the advice: a rubric item the judge failed stays failed."""
    cmds = ["git config --global credential.helper manager", "git push origin main"]
    bad = e2e.run_one(BY_NAME["auth_https_password"], 0, ARGS, "t",
                      recorded=recorded("auth_https_password", cmds, passed=False, llm_ok=False))
    good = e2e.run_one(BY_NAME["auth_https_password"], 1, ARGS, "t",
                       recorded=recorded("auth_https_password", cmds, passed=True, llm_ok=True))
    assert not bad["passed"] and any(r["type"] == "llm" and not r["ok"] for r in bad["rubric"])
    assert all(r["ok"] for r in good["rubric"] if r["type"] == "llm")


def test_tiers_are_rated_again_with_todays_code(harness):
    rec = recorded("push_rejected", ["git fetch origin", "git rebase", "git push origin main"], passed=False)
    for s in rec["answer"]["steps"]:
        s.update(tier="safe", tier_reason="an old rating")
    row = e2e.run_one(BY_NAME["push_rejected"], 0, ARGS, "t", recorded=rec)
    assert [s["tier"] for s in row["answer"]["steps"]] == ["safe", "careful", "careful"]
    assert rec["answer"]["steps"][1]["tier"] == "safe"            # the recorded row is not changed


def test_replay_command_writes_a_report_of_changed_verdicts(harness, monkeypatch):
    rows = [recorded("push_rejected", ["git fetch origin", "git rebase", "git push origin main"], passed=False),
            recorded("merge_conflict", ["git status"], passed=False)]
    src = harness / "results" / "git_e2e_assistant_tuned_20261127_010000.json"
    src.write_text(json.dumps({"summary": {"tag": "assistant_tuned", "answer_model": "groq/openai/gpt-oss-20b",
                                           "judge": "groq/openai/gpt-oss-120b"}, "rows": rows}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--replay", str(src)])
    e2e.main()
    out = json.loads(next((harness / "results").glob("git_e2e_replay_assistant_tuned_*.json")).read_text())
    assert out["summary"]["replayed_from"] == src.name
    assert out["summary"]["answer_model"] == "groq/openai/gpt-oss-20b"
    assert [r["passed"] for r in out["rows"]] == [True, False]
    md = next((harness / "results").glob("git_e2e_replay_assistant_tuned_*.md")).read_text()
    assert "| tuned | 0/2 | 1/2 |" in md
    assert "**push_rejected** (run 0): harness:" in md
    assert "**merge_conflict** (run 0) [assistant]" in md


def test_replay_accepts_a_run_still_in_progress(harness, monkeypatch):
    src = harness / "results" / "git_e2e_assistant_held_out_20261127_010000.partial.jsonl"
    src.write_text(json.dumps(recorded("ho_push_rejected_upstream",
                                       ["git pull --rebase upstream develop", "git push upstream develop"],
                                       passed=True)) + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--replay", str(src)])
    e2e.main()
    out = json.loads(next((harness / "results").glob("git_e2e_replay_assistant_held_out_*.json")).read_text())
    assert out["rows"][0]["passed"] and out["rows"][0]["held_out"]
