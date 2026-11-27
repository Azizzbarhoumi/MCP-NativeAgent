"""The knowledge eval must stop on a rate limit instead of skipping questions, keep every judged
question, and resume with exactly the same settings. Skipping would quietly change the question
set and make two runs incomparable."""

import json
import sys
from types import SimpleNamespace

import pytest
import yaml

from common import llm
from eval import run_knowledge_eval as rk

QUESTIONS = [{"id": f"q{i}", "type": "lookup", "question": f"question {i}?", "expected_answer": "x",
              "source_docs": [{"path": "a.md"}], "reviewed": True} for i in range(1, 4)]


@pytest.fixture
def evaldir(tmp_path, monkeypatch):
    (tmp_path / "knowledge_questions.yaml").write_text(yaml.safe_dump(QUESTIONS), encoding="utf-8")
    monkeypatch.setattr(rk, "EVAL", tmp_path)
    monkeypatch.setattr(rk, "flush", lambda: None)
    return tmp_path


def fake_answer(fail_on=None, asked=None):
    def answer_question(question, task_id, **kw):
        if asked is not None:
            asked.append(question)
        if question == fail_on:
            raise llm.ProviderUnavailable("429 from groq, retries exhausted")
        hit = SimpleNamespace(chunk_id="c1", source="s", path="a.md", section="", score=1.0)
        return SimpleNamespace(answer="ok [1]"), [hit], {"cited": [1]}
    return answer_question


def test_rate_limit_stops_and_resume_runs_only_the_rest(evaldir, monkeypatch):
    monkeypatch.setattr(rk, "judge", lambda *a: ("correct", "fine", 1))
    monkeypatch.setattr(rk, "answer_question", fake_answer(fail_on="question 2?"))
    monkeypatch.setattr(sys, "argv", ["x", "--judge", "groq"])
    with pytest.raises(SystemExit) as stop:
        rk.main()
    assert "nothing lost, 1 done" in str(stop.value) and "--resume" in str(stop.value)
    partial = next((evaldir / "results").glob("*.partial.jsonl"))
    assert len(partial.read_text().splitlines()) == 2              # settings line + q1

    asked = []
    monkeypatch.setattr(rk, "answer_question", fake_answer(asked=asked))
    monkeypatch.setattr(sys, "argv", ["x", "--judge", "groq", "--resume", str(partial)])
    rk.main()
    assert asked == ["question 2?", "question 3?"]                 # q1 is not asked again
    final = json.loads(next((evaldir / "results").glob("knowledge_*.json")).read_text())
    assert [r["id"] for r in final["rows"]] == ["q1", "q2", "q3"] and final["skipped"] == []
    assert not partial.exists()


def test_judge_rate_limit_also_stops(evaldir, monkeypatch):
    def judge(*a):
        raise llm.ProviderUnavailable("judge rate-limited")
    monkeypatch.setattr(rk, "judge", judge)
    monkeypatch.setattr(rk, "answer_question", fake_answer())
    monkeypatch.setattr(sys, "argv", ["x", "--judge", "groq"])
    with pytest.raises(SystemExit) as stop:
        rk.main()
    assert "nothing lost, 0 done" in str(stop.value)


def test_resume_refuses_other_settings(evaldir, monkeypatch):
    monkeypatch.setattr(rk, "judge", lambda *a: ("correct", "fine", 1))
    monkeypatch.setattr(rk, "answer_question", fake_answer(fail_on="question 2?"))
    monkeypatch.setattr(sys, "argv", ["x", "--judge", "groq", "--check"])
    with pytest.raises(SystemExit):
        rk.main()
    partial = next((evaldir / "results").glob("*.partial.jsonl"))
    monkeypatch.setattr(sys, "argv", ["x", "--judge", "groq", "--check", "--decompose", "--resume", str(partial)])
    with pytest.raises(SystemExit) as stop:
        rk.main()
    assert "cannot resume" in str(stop.value)
