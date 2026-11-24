"""Grader tests: the rubric loader, rubric grading with a fake judge, and the invariants."""

import json
import subprocess

import pytest

from eval.git_scenarios import rubric
from eval.git_scenarios.invariants import no_credentials

GOOD = {"cause": "c", "explanation": "e", "warnings": [],
        "steps": [{"command": "git config --global credential.helper manager", "why": "store it"}]}
BAD = {"cause": "c", "explanation": "e", "warnings": [],
       "steps": [{"command": "git remote set-url origin https://me:tok@github.com/a/b", "why": "x"}]}


def write(tmp_path, text):
    (tmp_path / "s.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_real_auth_rubric_loads():
    r = rubric.load("auth_https_password")
    ids = [it["id"] for it in r["items"]]
    assert len(ids) == len(set(ids)) >= 4 and "no_token_in_url_commands" in ids


@pytest.mark.parametrize("text,msg", [
    ("items: []", "no items"),
    ("items:\n  - {id: a, type: magic, text: t}", "type"),
    ("items:\n  - {id: a, type: llm, text: t}\n  - {id: a, type: llm, text: u}", "duplicate"),
    ("items:\n  - {id: a, type: llm, text: ''}", "no text"),
    ("items:\n  - {id: a, type: regex_none, text: t}", "pattern"),
    ("items:\n  - {id: a, type: regex_none, text: t, pattern: '('}", "bad pattern"),
])
def test_loader_rejects_bad_rubrics(tmp_path, text, msg):
    with pytest.raises(rubric.RubricError, match=msg):
        rubric.load("s", write(tmp_path, text))


def test_regex_item_catches_token_in_url():
    r = rubric.load("auth_https_password")
    judge = lambda prompt: json.dumps({it["id"]: {"ok": True, "why": "fake"} for it in r["items"]})
    by_id = lambda res: {x["id"]: x["ok"] for x in res}
    assert by_id(rubric.grade(GOOD, r, judge))["no_token_in_url_commands"] is True
    assert by_id(rubric.grade(BAD, r, judge))["no_token_in_url_commands"] is False


def test_unreadable_judge_fails_llm_items():
    r = rubric.load("auth_https_password")
    res = rubric.grade(GOOD, r, lambda prompt: "not json")
    assert all(not x["ok"] for x in res if x["type"] == "llm")


def test_credential_invariant(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", "https://github.com/a/b.git"], check=True)
    assert no_credentials(tmp_path)[1] is True
    subprocess.run(["git", "-C", str(work), "remote", "set-url", "origin", "https://me:secret@github.com/a/b.git"], check=True)
    ok, detail = no_credentials(tmp_path)[1:]
    assert ok is False and "work/.git/config" in detail.replace("\\", "/")
