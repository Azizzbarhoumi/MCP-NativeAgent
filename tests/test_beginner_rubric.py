"""The beginner explanation layer: 5 items graded on every answer, in the same judge call."""

import json

from eval.git_scenarios import rubric

B = rubric.load("_beginner")


def test_five_items_all_prefixed():
    assert len(B["items"]) == 5 and all(it["id"].startswith("beginner_") for it in B["items"])


def test_every_command_needs_a_real_reason():
    item = {"items": [i for i in B["items"] if i["type"] == "every_step_why"]}
    good = {"steps": [{"command": "git merge --abort", "why": "Stops the merge and puts every file back as before."},
                      {"command": "", "why": "Open app.py"}]}                 # manual steps are not commands
    short = {"steps": [{"command": "git merge --abort", "why": "abort it"}]}
    none = {"steps": [{"command": "", "why": "Create a token on GitHub"}]}
    assert rubric.grade(good, item, None)[0]["ok"]
    assert not rubric.grade(short, item, None)[0]["ok"]
    assert not rubric.grade(none, item, None)[0]["ok"]


def test_e2e_judge_grades_beginner_items_in_the_same_call(monkeypatch):
    from common import llm
    from eval import run_git_e2e as e2e
    calls = []

    def fake_chat(messages, **kw):
        calls.append(messages[0]["content"])
        items = {it["id"]: {"ok": True, "why": "fine"} for it in B["items"] if it["type"] == "llm"}
        items["explains_cause"] = {"ok": True, "why": "fine"}
        return json.dumps({"diagnosis": "correct", "reason": "ok", "items": items})

    monkeypatch.setattr(llm, "chat", fake_chat)
    key = {"expected_cause": ["x"], "risk_note": "r"}
    rub = {"items": [{"id": "explains_cause", "type": "llm", "text": "t"}]}
    ans = {"cause": "c", "explanation": "e", "warnings": [],
           "steps": [{"command": "git status", "why": "Shows which files changed and what git expects next."}]}
    verdict, graded, beginner = e2e.judge(key, ans, rub, "t")
    assert len(calls) == 1 and "beginner_jargon_explained" in calls[0]
    assert verdict["diagnosis"] == "correct" and graded[0]["ok"]
    assert [b["ok"] for b in beginner] == [True] * 5
