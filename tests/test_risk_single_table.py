"""One risk table: the tier the assistant SHOWS for a command (risk.py, on text) must equal the
tier the tool ENFORCES when it runs it (execute.py, on typed operations), in real repo states.
Both read tiers.py; this test fails if they ever drift apart again.
"""

import inspect
import os
import subprocess

import pytest

from git_assistant.translate import to_ops
from mcp_servers.git_local import execute as ex
from mcp_servers.git_local import inspect_repo as ir
from mcp_servers.git_local import risk, tiers

ENV = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **ENV}).stdout


def commit(repo, name, text):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", f"edit {name}")


@pytest.fixture
def root(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GIT_TOOL_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setattr(ex, "LOG", tmp_path / "exec.jsonl")
    return allowed


def make(root, state: str):
    """A repo with a bare 'origin', in one of the states the tiers depend on."""
    origin, repo = root / f"{state}.git", root / state
    git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    git(root, "clone", "-q", str(origin), str(repo))
    git(repo, "switch", "-q", "-c", "main")
    commit(repo, "a.txt", "a\n")
    git(repo, "branch", "feature")                                  # one commit behind main
    commit(repo, "a2.txt", "second\n")
    git(repo, "push", "-q", "-u", "origin", "main")
    if state in ("local", "behind"):
        commit(repo, "b.txt", "local work\n")                       # 1 commit only here
    if state == "behind":                                           # the remote moved on too
        other = root / "other"
        git(root, "clone", "-q", str(origin), str(other))
        commit(other, "c.txt", "teammate\n")
        git(other, "push", "-q", "origin", "main")
        git(repo, "fetch", "-q")
    if state == "detached_work":
        git(repo, "switch", "-q", "--detach")
        commit(repo, "d.txt", "on no branch\n")
    if state == "detached_clean":
        git(repo, "switch", "-q", "--detach")
    (repo / "a.txt").write_text("edited\n")                          # an unstaged edit
    (repo / "new.txt").write_text("untracked\n")
    return repo


COMMANDS = {   # every command must be valid in that state (none may be refused for its arguments)
    "local": ["git status", "git fetch origin", "git branch save", "git switch -c save", "git switch feature",
              "git add a.txt", "git add .", "git rm --cached a.txt", "git restore --staged a.txt",
              "echo new.txt >> .gitignore", "git commit -m 'msg'", "git stash", "git merge feature",
              "git pull origin main", "git pull --rebase origin main", "git rebase origin/main",
              "git revert HEAD", "git cherry-pick feature", "git reset --soft HEAD~1",
              "git commit --amend -m 'better'", "git push origin main", "git push -u origin main",
              "git reset --hard HEAD", "git push --force-with-lease origin main", "git clean -fd",
              "git branch -D feature"],
    "pushed": ["git pull --rebase origin main", "git rebase feature", "git commit --amend -m 'x'",
               "git reset --soft HEAD~1", "git push origin main", "git switch feature"],
    "behind": ["git pull --rebase origin main", "git pull origin main", "git push origin main",
               "git rebase origin/main", "git commit --amend -m 'x'"],
    "detached_work": ["git switch main", "git checkout main", "git switch -c save", "git branch save"],
    "detached_clean": ["git switch main", "git switch -c save"],
}


def text_tier(repo, cmd):
    return risk.in_context(cmd, ir.repo_state(str(repo)))["tier"]


def tool_tier(repo, cmd):
    tiers_seen = []
    for a in to_ops({"command": cmd, "why": ""}, repo):
        if a.kind == "op":
            p = ex.prepare(str(repo), a.op, a.params)
            assert p["tier"] != "blocked", (cmd, p)
            tiers_seen.append(p["tier"])
        else:
            assert a.kind == "read", (cmd, a)
    return tiers.worst(tiers_seen)


@pytest.mark.parametrize("state", sorted(COMMANDS))
def test_plan_and_tool_agree_on_every_tier(root, state):
    repo = make(root, state)
    disagree = {cmd: (text_tier(repo, cmd), tool_tier(repo, cmd)) for cmd in COMMANDS[state]}
    disagree = {c: v for c, v in disagree.items() if v[0] != v[1]}
    assert not disagree, f"plan tier vs tool tier in state {state}: {disagree}"


def test_the_states_really_change_the_tiers(root):
    """Guard against a vacuous pass: the state rules must actually fire."""
    assert tool_tier(make(root, "local"), "git pull --rebase origin main") == "careful"
    assert tool_tier(make(root, "pushed"), "git rebase feature") == "destructive"
    assert tool_tier(make(root, "detached_work"), "git switch main") == "destructive"
    assert tool_tier(make(root, "detached_clean"), "git switch main") == "safe"


def test_pull_rebase_settled_as_written():
    """The rationale in tiers.py: careful for local-only commits, destructive once pushed."""
    assert "git pull --rebase" in inspect.getdoc(tiers)
    assert tiers.decide("rebase", {"rewrites_pushed": False})[0] == "careful"
    assert tiers.decide("rebase", {"rewrites_pushed": True})[0] == "destructive"
    assert risk.classify("git pull --rebase origin main")["key"] == "rebase"


def test_every_tool_operation_and_rule_is_in_the_one_table():
    assert set(ex.OPS) <= set(tiers.TABLE)
    assert {key for _, key in risk.RULES} <= set(tiers.TABLE)
    assert all(t in tiers.TIERS for t, _ in tiers.TABLE.values())
    # no second table hiding in the executor
    assert "tier" not in inspect.signature(ex.Op).parameters
    assert not any(hasattr(op, "tier") for op in ex.OPS.values())


def test_every_operation_is_checked_in_some_state(root):
    covered = set()
    for state, cmds in COMMANDS.items():
        repo = make(root, state)
        covered |= {a.op for cmd in cmds for a in to_ops({"command": cmd, "why": ""}, repo) if a.kind == "op"}
    mid_merge = {"resolve_conflict", "merge_abort"}            # checked in the mid-merge test below
    not_yet = {"merge_commit", "rebase_abort", "rebase_continue", "stash_pop"}   # base tier only
    assert set(ex.OPS) - covered - mid_merge == not_yet, set(ex.OPS) - covered - mid_merge
    assert all(tiers.decide(op)[0] == "safe" for op in not_yet - {"rebase_continue"})


def test_states_mid_operation_match_too(root):
    """A merge stopped on a conflict: resolving, aborting and finishing it."""
    repo = make(root, "local")
    git(repo, "stash", "-q", "-u")
    git(repo, "switch", "-q", "feature")
    commit(repo, "a.txt", "theirs\n")
    git(repo, "switch", "-q", "main")
    commit(repo, "a.txt", "ours\n")
    subprocess.run(["git", "merge", "feature"], cwd=repo, capture_output=True, env={**os.environ, **ENV})
    for cmd in ["git checkout --theirs -- a.txt", "git merge --abort", "git switch feature"]:
        assert text_tier(repo, cmd) == tool_tier(repo, cmd), cmd


def test_bare_pull_and_rebase_use_the_upstream_even_with_another_name(root):
    """`git pull --rebase` / `git rebase` with no arguments replay onto the branch's upstream.
    The translator used origin/<this branch>, which is wrong when the branch tracks another name
    (here `work` tracks origin/main); the plan's text rating now uses the same default."""
    repo = make(root, "local")
    git(repo, "switch", "-q", "-c", "work", "--track", "origin/main")
    commit(repo, "w.txt", "local only\n")
    ops = lambda cmd: [(a.op, a.params) for a in to_ops({"command": cmd, "why": ""}, repo) if a.kind == "op"]
    assert ops("git pull --rebase") == [("fetch", {"remote": "origin"}), ("rebase", {"onto": "origin/main"})]
    assert ops("git pull") == [("pull", {"remote": "origin", "branch": "main"})]
    assert ops("git rebase") == [("rebase", {"onto": "origin/main"})]
    for cmd in ("git pull --rebase", "git rebase", "git pull --rebase origin"):
        assert text_tier(repo, cmd) == tool_tier(repo, cmd) == "careful", cmd
    git(repo, "switch", "-q", "-c", "lonely", "--no-track")
    assert [a.kind for a in to_ops({"command": "git rebase", "why": ""}, repo)] == ["unsupported"]
