"""Orchestrator tests with a fake model and a fake tool: no network, no Docker, no keys."""

import asyncio
import json

import orchestrator.graph as graph_mod
from common.schemas import Evidence
from orchestrator.graph import MAX_STEPS, build_graph, initial_state
from orchestrator.mcp_client import ToolError

ISSUES = {"issues": [
    {"number": 1, "title": "Bug A", "url": "https://github.com/me/test/issues/1"},
    {"number": 2, "title": "Bug B", "url": "https://github.com/me/test/issues/2"},
]}
CALL = json.dumps({"action": "list_issues", "args": {"owner": "me", "repo": "test", "state": "OPEN"}})
ANSWER = json.dumps({"action": "answer", "answer": "Two open issues: Bug A, Bug B."})


class FakeTools:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def call(self, name, args):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def run(monkeypatch, replies, tools):
    it = iter(replies)
    monkeypatch.setattr(graph_mod, "chat", lambda *a, **k: next(it))
    return asyncio.run(build_graph(tools).ainvoke(initial_state("open issues?", "t-1", "- list_issues(owner, repo)", "me/test")))


def test_issues_become_evidence(monkeypatch):
    state = run(monkeypatch, [CALL, ANSWER], FakeTools(result=ISSUES))
    assert state["evidence"] == [
        Evidence(text="#1 Bug A", source="https://github.com/me/test/issues/1", tool="github.list_issues"),
        Evidence(text="#2 Bug B", source="https://github.com/me/test/issues/2", tool="github.list_issues"),
    ]
    assert state["final_answer"].startswith("Two open issues")


def test_step_limit_stops_the_loop(monkeypatch):
    # A model that asks for the tool forever must be stopped by the cap.
    state = run(monkeypatch, [CALL] * 50, FakeTools(result={"issues": []}))
    assert "step limit" in state["final_answer"]
    assert state["step_count"] <= MAX_STEPS + 1


def test_tool_failure_is_reported_not_invented(monkeypatch):
    tools = FakeTools(error=ToolError("Not Found: repo me/nope"))
    state = run(monkeypatch, [CALL, ANSWER], tools)
    assert "failed" in state["final_answer"] and "Not Found" in state["final_answer"]
    assert state["evidence"] == []
    assert tools.calls == 1  # a GitHub error is not retried


def test_disallowed_tool_is_rejected(monkeypatch):
    # The model asks for a tool outside the allowlist 3 times: nothing runs, loop stops.
    bad = json.dumps({"action": "create_issue", "args": {}})
    tools = FakeTools(result=ISSUES)
    state = run(monkeypatch, [bad] * 3, tools)
    assert tools.calls == 0
    assert "invalid output" in state["final_answer"]


def test_commits_and_prs_become_evidence():
    from orchestrator.graph import result_to_evidence
    commits = [{"sha": "abc1234def", "html_url": "https://github.com/me/test/commit/abc1234",
                "commit": {"message": "Fix bug\n\ndetails"}}]
    pr = {"number": 7, "title": "Feature", "html_url": "https://github.com/me/test/pull/7",
          "mergeable": False, "mergeable_state": "dirty"}
    assert result_to_evidence("list_commits", commits)[0].text == "abc1234 Fix bug"
    ev = result_to_evidence("pull_request_read", pr)[0]
    assert "mergeable=False" in ev.text and ev.tool == "github.pull_request_read"


def test_connection_error_is_retried_then_reported(monkeypatch):
    tools = FakeTools(error=ConnectionError("server down"))
    state = run(monkeypatch, [CALL], tools)
    assert tools.calls == 3  # 1 try + 2 retries
    assert "server down" in state["final_answer"]
