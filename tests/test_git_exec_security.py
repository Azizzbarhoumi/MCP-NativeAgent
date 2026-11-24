"""Security tests for the local git tool: each test tries one attack and expects a refusal.

Covers: path traversal and symlink escape, option injection through branch names, messages
and paths, a repo with a malicious hook / fsmonitor / filter config, approval tokens reused
or replayed for another command, and credentials in arguments or remote URLs.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_servers.git_local import execute as ex
from mcp_servers.git_local import inspect_repo as ir
from mcp_servers.git_local.safe_git import GitToolError

ENV = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **ENV}).stdout


@pytest.fixture
def root(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GIT_TOOL_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setattr(ex, "LOG", tmp_path / "exec.jsonl")
    return allowed


@pytest.fixture
def repo(root):
    r = root / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "init")
    git(r, "branch", "feature")
    return r


def approve_and_run(repo, op, params, confirm=False):
    p = ex.prepare(str(repo), op, params)
    assert p["tier"] != "blocked", p
    return ex.execute(p["approval_token"], str(repo), op, params, p.get("confirm_phrase") if confirm else None)


# ------------------------------------------------------------ paths

def test_path_traversal_outside_root_is_refused(root, repo):
    with pytest.raises(GitToolError):
        ir.repo_state(str(root / ".." / ".."))
    assert ex.prepare(str(repo / ".." / ".."), "stash", {})["tier"] == "blocked"


def test_file_argument_traversal_is_refused(repo):
    for bad in ["../../etc/passwd", "/etc/passwd", ".git/config", "-q"]:
        assert ex.prepare(str(repo), "add", {"paths": [bad]})["tier"] == "blocked", bad


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin rights on Windows")
def test_symlink_escape_is_refused(root, repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(GitToolError, match="symlink"):
        ir.repo_state(str(root / "link"))
    (repo / "evil").symlink_to(outside, target_is_directory=True)
    assert ex.prepare(str(repo), "add", {"paths": ["evil/x"]})["tier"] == "blocked"


# ------------------------------------------------------------ option injection

@pytest.mark.parametrize("op,params", [
    ("switch", {"branch": "--orphan=x"}),
    ("switch", {"branch": "-"}),
    ("create_branch", {"name": "x;rm -rf ."}),
    ("merge", {"branch": "--strategy=ours"}),
    ("delete_branch", {"name": "--all"}),
    ("revert", {"rev": "--no-commit"}),
    ("reset_hard", {"rev": "-q"}),
    ("commit", {"message": "--amend"}),
    ("commit", {"message": "-m"}),
    ("push", {"remote": "--receive-pack=evil", "branch": "main"}),
    ("rm_cached", {"paths": ["--pathspec-from-file=/etc/passwd"]}),
])
def test_option_injection_is_refused(repo, op, params):
    assert ex.prepare(str(repo), op, params)["tier"] == "blocked"


def test_unknown_and_blocked_ops(repo):
    for op in ["raw", "config", "remote_set_url", "delete_repo", "shell"]:
        assert ex.prepare(str(repo), op, {"args": "status"})["tier"] == "blocked"


# ------------------------------------------------------------ hostile repo config

@pytest.mark.parametrize("key,value", [
    ("core.fsmonitor", "touch PWNED"),
    ("core.hooksPath", "evilhooks"),
    ("filter.x.clean", "touch PWNED"),
    ("diff.x.textconv", "touch PWNED"),
    ("include.path", "/tmp/other.gitconfig"),
    ("core.worktree", "/"),
    ("alias.st", "!touch PWNED"),
    ("credential.helper", "!touch PWNED"),
])
def test_hostile_config_repo_is_refused(repo, key, value):
    git(repo, "config", key, value)
    with pytest.raises(GitToolError, match="refused"):
        ir.repo_state(str(repo))
    assert ex.prepare(str(repo), "stash", {})["tier"] == "blocked"
    assert not (repo / "PWNED").exists()


def test_malicious_hook_in_default_folder_never_runs(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ntouch PWNED\n")
    hook.chmod(0o755)
    (repo / "b.txt").write_text("b\n")
    assert approve_and_run(repo, "add", {"paths": ["b.txt"]})["ok"]
    assert approve_and_run(repo, "commit", {"message": "with hook present"})["ok"]
    assert not (repo / "PWNED").exists()


def test_caller_environment_does_not_leak(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_DIR", str(tmp_path))          # would redirect git elsewhere
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.fsmonitor=touch PWNED'")
    assert ir.repo_state(str(repo))["branch"] == "main"
    assert not (repo / "PWNED").exists()


# ------------------------------------------------------------ approval tokens

def test_token_cannot_be_reused(repo):
    p = ex.prepare(str(repo), "switch", {"branch": "feature"})
    assert ex.execute(p["approval_token"], str(repo), "switch", {"branch": "feature"})["ok"]
    again = ex.execute(p["approval_token"], str(repo), "switch", {"branch": "feature"})
    assert not again["ok"] and "already used" in again["refused"]


def test_token_for_other_command_is_refused(repo):
    p = ex.prepare(str(repo), "switch", {"branch": "feature"})
    r = ex.execute(p["approval_token"], str(repo), "delete_branch", {"name": "feature"})
    assert not r["ok"] and "different command" in r["refused"]
    p = ex.prepare(str(repo), "create_branch", {"name": "ok"})
    r = ex.execute(p["approval_token"], str(repo), "create_branch", {"name": "other"})
    assert not r["ok"]


def test_token_is_invalid_after_repo_changes(repo):
    p = ex.prepare(str(repo), "create_branch", {"name": "later"})
    (repo / "c.txt").write_text("c\n")
    git(repo, "add", "c.txt")
    git(repo, "commit", "-q", "-m", "moved HEAD")
    r = ex.execute(p["approval_token"], str(repo), "create_branch", {"name": "later"})
    assert not r["ok"] and "repo state" in r["refused"]


def test_forged_token_is_refused(repo):
    r = ex.execute("deadbeef.0000", str(repo), "stash", {})
    assert not r["ok"]


def test_dangerous_needs_phrase_and_makes_backup(repo):
    (repo / "a.txt").write_text("changed but not committed\n")
    p = ex.prepare(str(repo), "reset_hard", {"rev": "HEAD"})
    assert p["tier"] == "dangerous" and any("a.txt" in l for l in p["would_lose"])
    assert not ex.execute(p["approval_token"], str(repo), "reset_hard", {"rev": "HEAD"}, "yes")["ok"]
    p = ex.prepare(str(repo), "reset_hard", {"rev": "HEAD"})
    r = ex.execute(p["approval_token"], str(repo), "reset_hard", {"rev": "HEAD"}, p["confirm_phrase"])
    assert r["ok"] and r["backups"]
    refs = git(repo, "for-each-ref", "refs/assistant-backup")
    assert "/head" in refs and "/uncommitted" in refs


# ------------------------------------------------------------ credentials

def test_credentials_are_blocked(repo, root):
    assert ex.prepare(str(repo), "commit", {"message": "token ghp_abcdefghijklmnopqrstuvwxyz123456"})["tier"] == "blocked"
    (repo / ".env").write_text("KEY=x\n")
    assert ex.prepare(str(repo), "add", {"paths": ["."]})["tier"] == "blocked"
    bare = root / "origin.git"
    git(root, "init", "-q", "--bare", str(bare))
    git(repo, "remote", "add", "origin", "https://user:secret@example.invalid/repo.git")
    p = ex.prepare(str(repo), "push", {"remote": "origin", "branch": "main"})
    assert p["tier"] == "blocked" and "credential" in p["explain"]
    assert "secret" not in str(ir.repo_state(str(repo))["remotes"])


def test_staging_a_file_with_conflict_markers_is_blocked(repo):
    git(repo, "switch", "-q", "feature")
    (repo / "a.txt").write_text("feature\n"); git(repo, "commit", "-qam", "f")
    git(repo, "switch", "-q", "main")
    (repo / "a.txt").write_text("main\n"); git(repo, "commit", "-qam", "m")
    subprocess.run(["git", "merge", "feature"], cwd=repo, capture_output=True, env={**os.environ, **ENV})
    p = ex.prepare(str(repo), "add", {"paths": ["a.txt"]})
    assert p["tier"] == "blocked" and "conflict markers" in p["explain"]
