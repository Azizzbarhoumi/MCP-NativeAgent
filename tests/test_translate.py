"""Plan translation (git command text -> typed tool operations) and the end-to-end harness with
no LLM (the answer key's fix as the plan)."""

import argparse

import pytest

from eval.git_scenarios.scenarios import new_repo, run, write
from git_assistant.translate import to_ops


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_TOOL_ALLOWED_ROOTS", str(tmp_path))
    r = new_repo(tmp_path / "work")
    run(r, "branch", "feature")
    return r


def ops(repo, command, why=""):
    return [(a.kind, a.op, a.params) for a in to_ops({"command": command, "why": why}, repo, ["app.py"])]


@pytest.mark.parametrize("command,expected", [
    ("git switch feature", [("op", "switch", {"branch": "feature"})]),
    ("git checkout -b save", [("op", "switch_create", {"name": "save"})]),
    ("git switch -c fix main", [("op", "create_branch", {"name": "fix", "start": "main"}), ("op", "switch", {"branch": "fix"})]),
    ('git commit -m "Fix it"', [("op", "commit", {"message": "Fix it"})]),
    ("git commit --amend --no-edit", [("op", "amend", {"message": "Initial commit"})]),
    ("git push -u origin feature", [("op", "push", {"remote": "origin", "branch": "feature", "set_upstream": True})]),
    ("git push --force origin main", [("op", "push_force", {"remote": "origin", "branch": "main"})]),
    ("git push origin +main", [("op", "push_force", {"remote": "origin", "branch": "main"})]),
    ("git pull --rebase origin main", [("op", "fetch", {"remote": "origin"}), ("op", "rebase", {"onto": "origin/main"})]),
    ("git reset --hard HEAD~1", [("op", "reset_hard", {"rev": "HEAD~1"})]),
    ("git reset --soft HEAD~1", [("op", "reset_soft", {"rev": "HEAD~1"})]),
    ("git rm --cached .env", [("op", "rm_cached", {"paths": [".env"]})]),
    ("git rm -r --cached node_modules", [("op", "rm_cached", {"paths": ["node_modules"]})]),
    ("git restore --staged build", [("op", "restore_staged", {"paths": ["build"]})]),
    ("git checkout --theirs app.py", [("op", "resolve_conflict", {"path": "app.py", "side": "theirs"})]),
    ("git merge --abort", [("op", "merge_abort", {})]),
    ("git rebase --continue", [("op", "rebase_continue", {})]),
    ("git stash && git switch feature && git stash pop",
     [("op", "stash", {}), ("op", "switch", {"branch": "feature"}), ("op", "stash_pop", {})]),
    ("echo .env >> .gitignore", [("op", "gitignore_add", {"pattern": ".env"})]),
    ("git config --global credential.helper manager", [("op", "config", {"args": "--global credential.helper manager"})]),
    ("git remote set-url origin git@github.com:a/b.git", [("op", "remote_set_url", {"args": "origin git@github.com:a/b.git"})]),
])
def test_commands_become_ops(repo, command, expected):
    assert ops(repo, command) == expected


@pytest.mark.parametrize("command,kind", [
    ("git status", "read"), ("git log --oneline", "read"), ("git reflog", "read"), ("git branch", "read"),
    ("git remote -v", "read"), ("git diff --staged", "read"),
    ("ssh -T git@github.com", "outside"), ("gh auth login", "outside"), ("rm -rf node_modules", "outside"),
    ("git checkout -- app.py", "unsupported"), ("git restore app.py", "unsupported"),
    ("git branch -f feature main", "unsupported"), ("git reset HEAD~1", "unsupported"),
    ("git -c core.sshCommand=evil push", "unsupported"), ("git rm app.py", "unsupported"),
])
def test_other_kinds(repo, command, kind):
    assert [k for k, _, _ in ops(repo, command)] == [kind]


def test_commit_all_stages_modified_tracked_files(repo):
    write(repo / "app.py", "changed\n")
    assert ops(repo, 'git commit -am "x"') == [("op", "add", {"paths": ["app.py"]}), ("op", "commit", {"message": "x"})]


def test_plain_commit_during_merge_finishes_the_merge(repo):
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n")
    assert ops(repo, "git commit") == [("op", "merge_commit", {})]


def test_manual_steps(repo):
    assert ops(repo, "", "Open app.py and remove the conflict markers") == [("manual", "", {})]
    assert ops(repo, "", "Add `node_modules/` and `build/` to .gitignore") == [
        ("op", "gitignore_add", {"pattern": "node_modules/"}), ("op", "gitignore_add", {"pattern": "build/"})]
    a = to_ops({"command": "", "why": "Create a token on GitHub"}, repo, ["app.py"])[0]
    assert a.kind == "manual" and a.note        # recorded, not simulated


@pytest.mark.parametrize("sid", ["merge_conflict", "push_rejected", "detached_head", "no_upstream", "ci_test_failure",
                                 "push_protection_secret", "big_files_staged", "auth_https_password"])
def test_e2e_oracle_passes(sid, tmp_path, monkeypatch):
    """The harness end to end, no LLM: the answer key's first fix must pass."""
    from eval import run_git_e2e as e2e
    from eval.git_scenarios.scenarios import SCENARIOS
    from mcp_servers.git_local import execute
    monkeypatch.setattr(e2e, "BUILD", tmp_path / "e2e")
    monkeypatch.setenv("GIT_TOOL_ALLOWED_ROOTS", str(tmp_path / "e2e"))
    monkeypatch.setattr(execute, "LOG", tmp_path / "exec.jsonl")
    build = next(b for b in SCENARIOS if b.__name__ == sid)
    row = e2e.run_one(build, 0, argparse.Namespace(oracle=True, no_judge=True, dangerous="approve"), "t")
    assert row["passed"], (row["failure"], row["steps"])
    assert row["dangerous_without_backup"] == 0
