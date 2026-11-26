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


# ============================================================ Phase 4 (overnight): more attacks

def bare_remote(root, repo):
    bare = root / "origin.git"
    git(root, "init", "-q", "--bare", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "main")
    return bare


# ------------------------------------------------------------ command injection (there is no shell)

@pytest.mark.parametrize("name", ["$(touch PWNED)", "`touch PWNED`", "a;touch PWNED", "a|b", "a&&b", "a\nb", "a b"])
def test_shell_metacharacters_in_names_are_refused(repo, name):
    assert ex.prepare(str(repo), "create_branch", {"name": name})["tier"] == "blocked"
    assert ex.prepare(str(repo), "switch", {"branch": name})["tier"] == "blocked"
    assert not (repo / "PWNED").exists()


def test_shell_syntax_in_a_commit_message_stays_text(repo):
    (repo / "b.txt").write_text("b\n")
    assert approve_and_run(repo, "add", {"paths": ["b.txt"]})["ok"]
    msg = "$(touch PWNED) `touch PWNED2` ; touch PWNED3"
    assert approve_and_run(repo, "commit", {"message": msg})["ok"]
    assert not any((repo / n).exists() for n in ("PWNED", "PWNED2", "PWNED3"))
    assert git(repo, "log", "-1", "--format=%s").strip() == msg


@pytest.mark.parametrize("pattern", ["x\n*", "$(touch PWNED)", "-x", "a b", "../x;y"])
def test_bad_gitignore_patterns_are_refused(repo, pattern):
    assert ex.prepare(str(repo), "gitignore_add", {"pattern": pattern})["tier"] == "blocked"


# ------------------------------------------------------------ path traversal, Windows/macOS spellings

@pytest.mark.parametrize("bad", ["..\\..\\outside", ".GIT/config", ".git./config", ".git /config", ".Git/hooks/pre-commit",
                                 "GIT~1/config", "sub/.git/config", "app.py:stream", "a.txt::$DATA", "C:\\Windows\\win.ini",
                                 "C:/Windows/win.ini", "\\\\server\\share\\x", "~/.ssh/id_rsa", "sub/../../x", "./../x"])
def test_path_spellings_that_escape_or_reach_git_dir_are_refused(repo, bad):
    for op, params in [("add", {"paths": [bad]}), ("rm_cached", {"paths": [bad]}),
                       ("restore_staged", {"paths": [bad]}), ("resolve_conflict", {"path": bad, "side": "ours"})]:
        assert ex.prepare(str(repo), op, params)["tier"] == "blocked", (op, bad)


def test_backslash_paths_inside_the_repo_still_work(repo):
    (repo / "src").mkdir()
    (repo / "src" / "m.py").write_text("x\n")
    assert ex.prepare(str(repo), "add", {"paths": ["src\\m.py"]})["tier"] == "recoverable"


# ------------------------------------------------------------ option injection

@pytest.mark.parametrize("op,params", [
    ("fetch", {"remote": "--upload-pack=touch PWNED"}),
    ("pull", {"remote": "origin", "branch": "-c core.sshCommand=touch PWNED"}),
    ("push", {"remote": "origin", "branch": "--receive-pack=touch PWNED"}),
    ("push", {"remote": "-c", "branch": "main"}),
    ("revert", {"rev": "--output=/tmp/x"}),
    ("cherry_pick", {"rev": "--exec=touch PWNED"}),
    ("rebase", {"onto": "--exec=touch PWNED"}),
    ("create_branch", {"name": "ok", "start": "--orphan"}),
    ("amend", {"message": "--fixup=HEAD"}),
])
def test_more_option_injection_is_refused(root, repo, op, params):
    bare_remote(root, repo)
    assert ex.prepare(str(repo), op, params)["tier"] == "blocked"
    assert not (repo / "PWNED").exists()


@pytest.mark.parametrize("args", [["-c", "core.sshCommand=touch PWNED", "fetch", "origin"],
                                  ["-C", "/", "status"], ["--exec-path=/tmp", "status"],
                                  ["fetch", "--upload-pack=touch PWNED", "origin"], ["--config-env=x=y", "status"]])
def test_last_line_of_defense_refuses_global_and_exec_options(repo, args):
    from mcp_servers.git_local import safe_git
    with pytest.raises(GitToolError):
        safe_git.run(repo, args, read_only=True)
    assert not (repo / "PWNED").exists()


@pytest.mark.parametrize("key,value", [("core.sshCommand", "touch PWNED"), ("core.alternateRefsCommand", "touch PWNED"),
                                       ("submodule.x.url", "ext::sh -c touch% PWNED"), ("trailer.x.command", "touch PWNED"),
                                       ("remote.origin.uploadpack", "touch PWNED"), ("url.evil.insteadOf", "origin")])
def test_more_hostile_config_is_refused(repo, key, value):
    git(repo, "config", key, value)
    assert ex.prepare(str(repo), "stash", {})["tier"] == "blocked"
    assert not (repo / "PWNED").exists()


@pytest.mark.parametrize("url", ["ext::sh -c touch% PWNED", "ssh://-oProxyCommand=touch%20PWNED/x"])
def test_dangerous_remote_transports_never_run_a_command(repo, url):
    git(repo, "remote", "add", "origin", url)
    p = ex.prepare(str(repo), "fetch", {"remote": "origin"})
    if p["tier"] != "blocked":
        r = ex.execute(p["approval_token"], str(repo), "fetch", {"remote": "origin"})
        assert not r["ok"]
    assert not (repo / "PWNED").exists() and not Path("PWNED").exists()


def test_token_as_url_user_name_is_blocked(repo):
    git(repo, "remote", "add", "origin", "https://abc123notatoken0000@example.invalid/a/b.git")
    p = ex.prepare(str(repo), "push", {"remote": "origin", "branch": "main"})
    assert p["tier"] == "blocked" and "credential" in p["explain"]
    git(repo, "remote", "set-url", "origin", "ssh://git@example.invalid/a/b.git")     # a normal ssh user is fine
    assert ex.prepare(str(repo), "push", {"remote": "origin", "branch": "main"})["tier"] == "recoverable"


# ------------------------------------------------------------ hooks, local and on the remote

HOOKS = ["pre-commit", "prepare-commit-msg", "commit-msg", "post-commit", "post-checkout", "post-merge", "pre-merge-commit",
         "pre-rebase", "post-rewrite", "pre-push", "reference-transaction", "pre-auto-gc"]


def test_no_local_hook_runs_for_any_operation(root, repo):
    bare_remote(root, repo)
    for name in HOOKS:
        h = repo / ".git" / "hooks" / name
        h.write_text(f"#!/bin/sh\ntouch {repo / ('PWNED-' + name)}\n")
        h.chmod(0o755)
    (repo / "b.txt").write_text("b\n")
    steps = [("add", {"paths": ["b.txt"]}), ("commit", {"message": "b"}), ("amend", {"message": "b2"}),
             ("switch", {"branch": "feature"}), ("merge", {"branch": "main"}), ("switch", {"branch": "main"}),
             ("revert", {"rev": "HEAD"}), ("push", {"remote": "origin", "branch": "main"}),
             ("switch", {"branch": "feature"}), ("cherry_pick", {"rev": "main"}), ("stash", {}),
             ("rebase", {"onto": "main"})]
    for op, params in steps:
        r = approve_and_run(repo, op, params, confirm=True)
        assert r["ok"] or "nothing to commit" in r.get("output", "") or "No local changes" in r.get("output", ""), (op, r)
    pwned = sorted(p.name for p in repo.glob("PWNED*"))
    assert pwned == [], pwned


def test_push_to_a_local_remote_with_hooks_is_refused(root, repo):
    """Found tonight: git runs a LOCAL remote's own pre-receive/update/post-receive hooks on push,
    and our -c overrides do not reach it. Such a push is now refused before git runs."""
    bare = bare_remote(root, repo)
    for name in ("pre-receive", "update", "post-receive", "post-update", "reference-transaction"):
        h = bare / "hooks" / name
        h.write_text(f"#!/bin/sh\ntouch {repo / ('PWNED-remote-' + name)}\n")
        h.chmod(0o755)
    (repo / "c.txt").write_text("c\n")
    approve_and_run(repo, "add", {"paths": ["c.txt"]})
    approve_and_run(repo, "commit", {"message": "c"})
    for op, params in [("push", {"remote": "origin", "branch": "main"}), ("fetch", {"remote": "origin"}),
                       ("pull", {"remote": "origin", "branch": "main"})]:
        p = ex.prepare(str(repo), op, params)
        assert p["tier"] == "blocked" and "hooks" in p["explain"], (op, p)
    assert sorted(p.name for p in repo.glob("PWNED*")) == []


def test_local_remote_outside_the_allowed_folder_is_refused(tmp_path, repo):
    outside = tmp_path / "outside.git"
    git(tmp_path, "init", "-q", "--bare", str(outside))
    git(repo, "remote", "add", "origin", str(outside))
    p = ex.prepare(str(repo), "push", {"remote": "origin", "branch": "main"})
    assert p["tier"] == "blocked" and "outside the allowed folders" in p["explain"]


def test_local_remote_with_hostile_config_is_refused(root, repo):
    bare = bare_remote(root, repo)
    git(bare, "config", "core.alternateRefsCommand", "touch PWNED")
    assert ex.prepare(str(repo), "push", {"remote": "origin", "branch": "main"})["tier"] == "blocked"


# ------------------------------------------------------------ unapproved high-risk steps

def test_dangerous_step_needs_its_own_exact_phrase(repo):
    (repo / "a.txt").write_text("dirty\n")
    for wrong in [None, "", "yes", "I accept the risk: clean", "i accept the risk: reset_hard"]:
        p = ex.prepare(str(repo), "reset_hard", {"rev": "HEAD"})
        r = ex.execute(p["approval_token"], str(repo), "reset_hard", {"rev": "HEAD"}, wrong)
        assert not r["ok"] and "confirmation phrase" in r["refused"], wrong
    assert (repo / "a.txt").read_text() == "dirty\n"


def test_expired_token_is_refused(repo, monkeypatch):
    p = ex.prepare(str(repo), "create_branch", {"name": "later"})
    real = ex.time.time
    monkeypatch.setattr(ex.time, "time", lambda: real() + ex.TOKEN_TTL_S + 1)
    r = ex.execute(p["approval_token"], str(repo), "create_branch", {"name": "later"})
    assert not r["ok"] and "expired" in r["refused"]


def test_token_for_one_repo_is_refused_in_another(root, repo):
    other = root / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    (other / "x").write_text("x\n"); git(other, "add", "x"); git(other, "commit", "-q", "-m", "x")
    p = ex.prepare(str(repo), "create_branch", {"name": "z"})
    r = ex.execute(p["approval_token"], str(other), "create_branch", {"name": "z"})
    assert not r["ok"] and "different command or repo state" in r["refused"]


def test_step_cannot_become_dangerous_after_approval(repo):
    """Approved while harmless; then a commit is made in detached HEAD (switching would now lose
    it). HEAD moved, so the old approval no longer counts and the new prepare says dangerous."""
    git(repo, "switch", "-q", "--detach")
    p = ex.prepare(str(repo), "switch", {"branch": "main"})
    assert p["tier"] == "recoverable"
    (repo / "d.txt").write_text("d\n"); git(repo, "add", "d.txt"); git(repo, "commit", "-q", "-m", "detached work")
    r = ex.execute(p["approval_token"], str(repo), "switch", {"branch": "main"})
    assert not r["ok"] and "repo state" in r["refused"]
    assert ex.prepare(str(repo), "switch", {"branch": "main"})["tier"] == "dangerous"


@pytest.mark.parametrize("op,params", [("resolve_conflict", {"path": "a.txt", "side": "sideways"}), ("switch", {}),
                                       ("add", {"paths": 5}), ("commit", {"message": None}), ("push", {"remote": "origin"}),
                                       ("reset_hard", {"rev": ["HEAD"]})])
def test_bad_parameters_are_refused_not_crashing(repo, op, params):
    assert ex.prepare(str(repo), op, params)["tier"] == "blocked"


# ------------------------------------------------------------ the model never holds the approval

def test_model_facing_prepare_hides_the_token_and_the_code(repo, capsys):
    p = ex.prepare_for_model(str(repo), "create_branch", {"name": "x"})
    assert "approval_token" not in p and p["step_id"]
    code = capsys.readouterr().err.rsplit("approval code: ", 1)[1].strip()
    assert code not in str(p)
    assert not ex.execute_with_code(p["step_id"], "WRONG1")["ok"]
    r = ex.execute_with_code(p["step_id"], code)
    assert r["ok"]
    again = ex.execute_with_code(p["step_id"], code)
    assert not again["ok"] and "unknown, used or cancelled" in again["refused"]


def test_three_wrong_codes_cancel_the_step(repo, capsys):
    p = ex.prepare_for_model(str(repo), "create_branch", {"name": "y"})
    code = capsys.readouterr().err.rsplit("approval code: ", 1)[1].strip()
    for guess in ("AAAAAA", "BBBBBB", "CCCCCC"):
        assert not ex.execute_with_code(p["step_id"], guess)["ok"]
    assert not ex.execute_with_code(p["step_id"], code)["ok"]
    assert "y" not in git(repo, "branch", "--format=%(refname:short)").split()


def test_mcp_server_tools_use_the_code_flow():
    from mcp_servers.git_local import server
    import inspect
    assert "approval_code" in inspect.signature(server.execute_step).parameters
    assert "approval_token" not in inspect.signature(server.execute_step).parameters


# ------------------------------------------------------------ secrets never leave the tool

def test_redact_hides_every_secret_shape():
    from mcp_servers.git_local.safe_git import redact
    text = ("+API_KEY=FAKE-not-a-real-key-0000\n token ghp_" + "A" * 36 + " and https://me:pw123@host/x\n"
            "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n"
            "export DB_PASSWORD='hunter2hunter2'\n")
    out = redact(text)
    for secret in ("FAKE-not-a-real-key", "A" * 36, "pw123", "abc\n", "hunter2"):
        assert secret not in out, secret
    keep = 'TOKEN = os.environ["DEPLOY_TOKEN"]\nremote: Password authentication is not supported\n'
    assert redact(keep) == keep


def test_diff_and_log_shown_to_the_model_are_redacted(repo):
    (repo / ".env").write_text("API_KEY=FAKE-not-a-real-key-0000\n")
    git(repo, "add", "-f", ".env")
    git(repo, "commit", "-q", "-m", "add key ghp_" + "B" * 36)
    (repo / ".env").write_text("API_KEY=FAKE-second-value-1111\n")
    out = ir.diff(str(repo)) + ir.log(str(repo)) + ir.show_commit(str(repo))
    assert "FAKE-second-value" not in out and "FAKE-not-a-real" not in out and "B" * 36 not in out
    assert ".env" in out                          # the file name is still visible


def test_tool_log_never_holds_a_secret(repo, root):
    git(repo, "remote", "add", "origin", "https://me:s3cretpass@example.invalid/a.git")
    ex.prepare(str(repo), "fetch", {"remote": "origin"}, task_id="leak-test")
    ex.prepare(str(repo), "commit", {"message": "ghp_" + "C" * 36}, task_id="leak-test")
    text = ex.LOG.read_text(encoding="utf-8")
    assert "s3cretpass" not in text and "C" * 36 not in text and "leak-test" in text
