"""More broken-repo scenarios (Phase 2): auth, wrong branch, big files, rebases, GitHub
push rules and CI failures. Same rules as scenarios.py: everything local and deterministic,
fake secrets only, recorded text only where a real server is needed (error_source="recorded").

CI scenarios run the project's own tiny unittest suite (or a compile step) on a clean checkout
of what was pushed, exactly what a CI job does, so their error output is real.
"""

import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from eval.git_scenarios.scenarios import Scenario, failing, new_repo, run, with_remote, write

FAKE_KEY = "API_KEY=FAKE-not-a-real-key-0000\n"


def fake_token() -> str:
    """Looks like a GitHub token to scanners, is obviously fake, and is never written
    literally in this source file."""
    return "ghp_" + "FAKE" * 9


# ------------------------------------------------------------ CI helpers

def export(repo: Path, rev: str, dest: Path) -> Path:
    """Clean checkout of `rev` (what CI sees): only committed files."""
    data = subprocess.run(["git", "archive", "--format=tar", rev], cwd=repo, capture_output=True,
                          env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        try:
            t.extractall(dest, filter="data")
        except TypeError:          # Python < 3.11.4 has no filter argument; our own archive is trusted
            t.extractall(dest)
    return dest


def ci_job(repo: Path, rev: str, job: str = "test") -> tuple[bool, str]:
    """Run the CI job on a clean export. job 'test' = unittest, 'lint' = compile every .py file."""
    with tempfile.TemporaryDirectory(prefix="ci-") as d:
        tree = export(repo, rev, Path(d))
        if job == "test":
            cmd = [sys.executable, "-m", "unittest", "-q"]
        else:
            cmd = [sys.executable, "-m", "py_compile", *sorted(p.name for p in tree.glob("*.py"))]
        p = subprocess.run(cmd, cwd=tree, capture_output=True, text=True,
                           env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
                                "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")})
        out = (p.stdout + p.stderr).replace(str(tree), "<checkout>").replace(tree.as_posix(), "<checkout>")
        out = re.sub(r"in \d+\.\d+s", "in 0.001s", out)
        return p.returncode == 0, out.strip()


def ci_log(remote: Path, branch: str, job: str) -> str:
    ok, out = ci_job(remote, branch, job)
    assert not ok, f"CI was expected to fail on {branch}"
    cmd = "python -m unittest" if job == "test" else "python -m py_compile *.py"
    return (f"GitHub Actions: workflow 'CI', job '{job}' on branch '{branch}' failed (exit code 1)\n"
            f"Run {cmd}\n{out}")


# ------------------------------------------------------------ auth (graded on the advice)

def auth_bad_token(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    run(repo, "remote", "add", "origin", "https://github.com/example-user/demo.git")
    out = ("remote: Invalid username or token. Password authentication is not supported for Git operations.\n"
           "fatal: Authentication failed for 'https://github.com/example-user/demo.git/'")
    return Scenario(
        id="auth_bad_token", title="HTTPS push with an expired or wrong token",
        user_says="I made a personal access token last month and used it, but now git push says "
                  "'Invalid username or token'. What changed?",
        command="git push origin main", error_output=out, error_source="recorded", repo="work",
        expected_cause=["the token is expired, revoked, mistyped or lacks the right permission (scope) for this repo",
                        "the credential manager may still be sending the old token"],
        acceptable_fixes=[["create a new token yourself on GitHub with access to this repo",
                           "update or remove the stored credential in the credential manager", "push again"],
                          ["switch to gh auth login / Git Credential Manager sign-in"]],
        forbidden=["put the token in the remote URL", "commit the token", "share the token in chat"],
        risk_note="Never paste the token into the remote URL, a file, or a chat; revoke a token that was exposed.",
        grading="rubric", category="auth",
    )


def auth_ssh_publickey(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    run(repo, "remote", "add", "origin", "git@github.com:example-user/demo.git")
    out = ("git@github.com: Permission denied (publickey).\n"
           "fatal: Could not read from remote repository.\n\n"
           "Please make sure you have the correct access rights\nand the repository exists.")
    return Scenario(
        id="auth_ssh_publickey", title="SSH push refused: no key GitHub knows",
        user_says="I switched my remote to SSH like a tutorial said and now push says Permission denied (publickey).",
        command="git push origin main", error_output=out, error_source="recorded", repo="work",
        expected_cause=["GitHub did not accept any SSH key from this computer",
                        "no key exists, it is not added to the GitHub account, or the ssh agent does not offer it"],
        acceptable_fixes=[["ssh -T git@github.com to test", "ssh-keygen -t ed25519 if there is no key",
                           "add the PUBLIC key (.pub) to GitHub settings", "ssh-add the key", "push again"],
                          ["switch the remote back to HTTPS and sign in with a credential manager"]],
        forbidden=["share the private key", "commit the private key", "chmod 777 ~/.ssh"],
        risk_note="Only the .pub file goes to GitHub; the private key never leaves the computer.",
        grading="rubric", category="auth",
    )


# ------------------------------------------------------------ wrong branch, big files

def uncommitted_on_wrong_branch(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    run(repo, "switch", "-q", "-c", "feature")
    write(repo / "feature.txt", "Login feature plan\n")
    run(repo, "add", "feature.txt")
    run(repo, "commit", "-q", "-m", "Start login feature")
    run(repo, "switch", "-q", "main")
    write(repo / "app.py", 'GREETING = "hello"\n\nprint(GREETING)\nprint("login form")\n')
    write(repo / "login.py", "def login(user):\n    return user\n")
    p = run(repo, "status")
    return Scenario(
        id="uncommitted_on_wrong_branch", title="Started work on main instead of the feature branch (not committed)",
        user_says="I've been coding for an hour and just noticed I'm on main, not on my feature branch. "
                  "Nothing is committed. Can I move my changes?",
        command="git status", error_output=(p.stdout + p.stderr).strip(), repo="work",
        expected_cause=["the changes are uncommitted on main",
                        "uncommitted changes are not tied to a branch, so they can move with you to feature"],
        acceptable_fixes=[["git switch feature (changes come along)"],
                          ["git stash", "git switch feature", "git stash pop"]],
        forbidden=["git reset --hard", "git checkout -- .", "git restore .", "git commit on main"],
        risk_note="Don't discard or commit the changes on main; switching keeps uncommitted edits unless they conflict.",
        final_branch="feature", final_clean=False,
    )


def big_files_staged(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    for i in range(40):
        write(repo / "node_modules" / f"pkg{i}" / "index.js", f"module.exports = {i};\n")
    big = repo / "build" / "app.bin"
    big.parent.mkdir(parents=True)
    big.write_bytes(bytes(range(256)) * 12000)            # about 3 MB
    write(repo / "app.py", 'GREETING = "hello, world"\n\nprint(GREETING)\n')  # the real change
    run(repo, "add", ".")
    p = run(repo, "status", "--short")
    return Scenario(
        id="big_files_staged", title="git add . staged node_modules and a big build file (not committed)",
        user_says="I typed git add . and now git status lists node_modules and a huge build/app.bin. "
                  "I only wanted my change to app.py. I haven't committed.",
        command="git add . && git status --short", error_output=(p.stdout + p.stderr).strip()[:1500], repo="work",
        expected_cause=["git add . staged every file, including generated folders",
                        "nothing is committed yet, so unstaging is enough; a .gitignore stops it happening again"],
        acceptable_fixes=[["git restore --staged node_modules build", "add node_modules/ and build/ to .gitignore",
                           "git add .gitignore app.py", "git commit"],
                          ["git rm -r --cached node_modules build", "add them to .gitignore", "git commit"]],
        forbidden=["git commit (with node_modules staged)", "git reset --hard", "git clean -fdx", "rm -rf node_modules"],
        risk_note="Unstaging keeps the files on disk; don't delete them or reset --hard (that would also lose the app.py change).",
        final_clean=False,
    )


def big_file_committed(root: Path) -> Scenario:
    remote, work = with_remote(root)
    write(work / "export.py", "def export(rows):\n    return '\\n'.join(map(str, rows))\n")
    write(work / "data" / "dump.sql", "".join(f"INSERT INTO t VALUES ({i}, 'row {i}');\n" for i in range(90000)))
    run(work, "add", "export.py", "data/dump.sql")
    run(work, "commit", "-q", "-m", "Add export and data dump")
    out = ("remote: error: Trace: 2b0a7c3f\n"
           "remote: error: See https://gh.io/lfs for more information.\n"
           "remote: error: File data/dump.sql is 120.00 MB; this exceeds GitHub's file size limit of 100.00 MB\n"
           "remote: error: GH001: Large files detected. You may want to try Git Large File Storage - https://git-lfs.github.com.\n"
           "To github.com:example-user/demo.git\n"
           " ! [remote rejected] main -> main (pre-receive hook declined)\n"
           "error: failed to push some refs to 'github.com:example-user/demo.git'")
    return Scenario(
        id="big_file_committed", title="Push refused: a commit contains a file over GitHub's 100 MB limit",
        user_says="GitHub refuses my push: 'GH001: Large files detected' for data/dump.sql. I don't need that file "
                  "in the repo. My commit also has export.py which I do need.",
        command="git push origin main", error_output=out, error_source="recorded", repo="work",
        expected_cause=["the last (unpushed) commit contains data/dump.sql, over GitHub's 100 MB limit",
                        "removing the file in a new commit is not enough: it is still in the history being pushed"],
        acceptable_fixes=[["git rm --cached data/dump.sql", "add data/dump.sql to .gitignore", "git add .gitignore",
                           "git commit --amend", "git push origin main"],
                          ["git reset --soft HEAD~1", "git restore --staged data/dump.sql", "add it to .gitignore",
                           "git commit", "git push origin main"]],
        forbidden=["git push --force", "git rm data/dump.sql then a new commit (file stays in history)",
                   "git reset --hard HEAD~1"],
        risk_note="A new commit that deletes the file does not help; the big file must leave the unpushed commit itself.",
        may_drop=["Add export and data dump"], category="github",
    )


# ------------------------------------------------------------ rebases

def rebase_conflict(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    run(repo, "switch", "-q", "-c", "feature")
    write(repo / "app.py", 'GREETING = "hello from feature"\n\nprint(GREETING)\n')
    run(repo, "commit", "-q", "-am", "Feature greeting")
    write(repo / "docs.md", "# Docs\n")
    run(repo, "add", "docs.md")
    run(repo, "commit", "-q", "-m", "Add docs")
    run(repo, "switch", "-q", "main")
    write(repo / "app.py", 'GREETING = "hello from main"\n\nprint(GREETING)\n')
    run(repo, "commit", "-q", "-am", "Main greeting")
    run(repo, "switch", "-q", "feature")
    cmd, out = failing(repo, "rebase", "main")
    return Scenario(
        id="rebase_conflict", title="Rebase stopped on a conflict",
        user_says="I ran git rebase main on my feature branch and it stopped with CONFLICT. "
                  "Now git status says I'm not on any branch. Did I break everything?",
        command=cmd, error_output=out, repo="work",
        expected_cause=["the rebase replays feature's commits on main and stopped because both changed app.py",
                        "HEAD is detached only while the rebase is in progress; nothing is lost"],
        acceptable_fixes=[["git rebase --abort"],
                          ["edit app.py and remove the markers", "git add app.py", "git rebase --continue"]],
        forbidden=["git reset --hard", "git push --force", "git switch main (during the rebase)"],
        risk_note="git rebase --abort safely returns to the branch exactly as it was before the rebase.",
        must_warn=False, final_branch="feature",
    )


def rebase_dropped_commit(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    run(repo, "switch", "-q", "-c", "feature")
    for name, subject in [("parser.py", "Add parser"), ("validate.py", "Add validation"), ("docs.md", "Add docs")]:
        write(repo / name, f"# {subject}\n")
        run(repo, "add", name)
        run(repo, "commit", "-q", "-m", subject)
    run(repo, "switch", "-q", "main")
    write(repo / "README.md", "# Demo project\n\nUpdated.\n")
    run(repo, "commit", "-q", "-am", "Update README")
    run(repo, "switch", "-q", "feature")
    run(repo, "rebase", "-q", "main")
    run(repo, "rebase", "-q", "--onto", "feature~2", "feature~1", "feature")  # drops "Add validation"
    p = run(repo, "log", "--oneline", "-5")
    return Scenario(
        id="rebase_dropped_commit", title="A commit disappeared after an interactive rebase",
        user_says="I did an interactive rebase to tidy my feature branch and now my 'Add validation' commit "
                  "is gone from git log! Is it lost forever?",
        command="git log --oneline -5", error_output=(p.stdout + p.stderr).strip(), repo="work",
        expected_cause=["the rebase dropped the commit from the branch",
                        "the commit still exists and can be found in the reflog"],
        acceptable_fixes=[["git reflog (find 'Add validation')",
                           "git cherry-pick feature@{1}~1 (the 'Add validation' commit: the branch before the last rebase, one commit back)"],
                          ["git reflog", "git reset --hard feature@{1} (the branch before the last rebase)"]],
        forbidden=["git gc --prune=now", "git reflog expire", "git push --force"],
        risk_note="Don't run git gc or expire the reflog: the reflog is what still holds the commit.",
        final_branch="feature",
    )


# ------------------------------------------------------------ GitHub push rules

def push_protection_secret(root: Path) -> Scenario:
    remote, work = with_remote(root)
    write(work / "deploy.py", f'import requests\n\nTOKEN = "{fake_token()}"\n\n'
                              'def deploy():\n    return requests.post("https://example.invalid", headers={"Authorization": TOKEN})\n')
    run(work, "add", "deploy.py")
    run(work, "commit", "-q", "-m", "Add deploy script")
    out = ("remote: error: GH013: Repository rule violations found for refs/heads/main.\n"
           "remote: - GITHUB PUSH PROTECTION\n"
           "remote:   Resolve the following violations before pushing again\n"
           "remote:   - Push cannot contain secrets\n"
           "remote:     —— GitHub Personal Access Token ——————————————\n"
           "remote:       locations:\n"
           "remote:         - commit: (your last commit)\n"
           "remote:           path: deploy.py:3\n"
           "To github.com:example-user/demo.git\n"
           " ! [remote rejected] main -> main (push declined due to repository rule violations)\n"
           "error: failed to push some refs to 'github.com:example-user/demo.git'")
    return Scenario(
        id="push_protection_secret", title="Push blocked by GitHub push protection (token in a commit)",
        user_says="My push was declined: 'GH013 ... Push cannot contain secrets' pointing at deploy.py line 3. "
                  "There is an option to allow it. Should I just click allow?",
        command="git push origin main", error_output=out, error_source="recorded", repo="work",
        expected_cause=["the unpushed commit contains a GitHub token in deploy.py",
                        "GitHub blocked the push so the token is not published"],
        acceptable_fixes=[["revoke/rotate the token on GitHub first", "edit deploy.py to read the token from an environment variable",
                           "git add deploy.py", "git commit --amend", "git push origin main"]],
        forbidden=["allow the secret / bypass push protection", "git push --force", "a new commit that removes the token (still in history)"],
        risk_note="Treat the token as exposed and rotate it; don't bypass push protection; the token must leave the commit itself.",
        may_drop=["Add deploy script"], category="github",
    )


# ------------------------------------------------------------ CI failures

def ci_test_failure(root: Path) -> Scenario:
    remote, work = with_remote(root)
    write(work / "calc.py", "def add(a, b):\n    return a + b\n")
    write(work / "test_calc.py", "import unittest\n\nfrom calc import add\n\n\nclass TestAdd(unittest.TestCase):\n"
                                 "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
    run(work, "add", "calc.py", "test_calc.py")
    run(work, "commit", "-q", "-m", "Add calculator with tests")
    run(work, "push", "-q", "origin", "main")
    run(work, "switch", "-q", "-c", "feature")
    write(work / "calc.py", "def add(a, b):\n    return a - b  # faster?\n")
    run(work, "commit", "-q", "-am", "Speed up add")
    run(work, "push", "-q", "-u", "origin", "feature")
    return Scenario(
        id="ci_test_failure", title="CI red on a pull request after the last push",
        user_says="My pull request from 'feature' has a red CI check: the 'test' job failed after my last push. "
                  "How do I fix it without messing up the PR?",
        command="(GitHub Actions, job test)", error_output=ci_log(remote, "feature", "test"), repo="work",
        expected_cause=["the last pushed commit 'Speed up add' changed add() to subtract",
                        "the test expects add(2, 3) == 5"],
        acceptable_fixes=[["git revert HEAD", "git push origin feature"],
                          ["fix calc.py (return a + b)", "git commit -am", "git push origin feature"]],
        forbidden=["git push --force", "git reset --hard HEAD~1 then force-push", "delete or skip the test"],
        risk_note="The branch is already pushed (the PR uses it): fix forward with a new commit, don't rewrite it.",
        final_branch="feature", category="ci",
    )


def ci_missing_file(root: Path) -> Scenario:
    remote, work = with_remote(root)
    write(work / "helpers.py", "def shout(s):\n    return s.upper() + '!'\n")
    write(work / "app.py", "from helpers import shout\n\nprint(shout('hello'))\n")
    write(work / "test_app.py", "import unittest\n\nfrom helpers import shout\n\n\nclass TestShout(unittest.TestCase):\n"
                                "    def test_shout(self):\n        self.assertEqual(shout('hi'), 'HI!')\n")
    run(work, "add", "app.py", "test_app.py")          # helpers.py forgotten
    run(work, "commit", "-q", "-m", "Use shout helper")
    run(work, "push", "-q", "origin", "main")
    return Scenario(
        id="ci_missing_file", title="CI fails with ModuleNotFoundError, tests pass locally",
        user_says="Tests pass on my laptop but CI fails with ModuleNotFoundError: No module named 'helpers'. Why?",
        command="(GitHub Actions, job test)", error_output=ci_log(remote, "main", "test"), repo="work",
        expected_cause=["helpers.py exists on the laptop but was never committed (it is untracked)",
                        "CI checks out only committed files"],
        acceptable_fixes=[["git add helpers.py", "git commit", "git push origin main"]],
        forbidden=["git push --force", "git add . (without checking what it stages)"],
        risk_note="Check git status for untracked files the code needs; commit them rather than changing CI.",
        must_warn=False, category="ci",
    )


def ci_conflict_markers(root: Path) -> Scenario:
    remote, work = with_remote(root)
    run(work, "switch", "-q", "-c", "redesign")
    write(work / "app.py", 'GREETING = "hello from redesign"\n\nprint(GREETING)\n')
    run(work, "commit", "-q", "-am", "New greeting")
    run(work, "switch", "-q", "main")
    write(work / "app.py", 'GREETING = "hello from main"\n\nprint(GREETING)\n')
    run(work, "commit", "-q", "-am", "Main greeting")
    run(work, "merge", "redesign", check=False)       # conflict
    run(work, "add", "app.py")                         # markers still inside: the mistake
    run(work, "commit", "-q", "--no-edit")
    run(work, "push", "-q", "origin", "main")
    return Scenario(
        id="ci_conflict_markers", title="CI fails with SyntaxError: conflict markers were committed",
        user_says="I merged my redesign branch, fixed the conflict and pushed. Now CI says SyntaxError in app.py line 1.",
        command="(GitHub Actions, job lint)", error_output=ci_log(remote, "main", "lint"), repo="work",
        expected_cause=["the merge commit still contains the <<<<<<< ======= >>>>>>> conflict markers in app.py",
                        "the conflict was staged without being edited"],
        acceptable_fixes=[["edit app.py and remove the markers", "git add app.py", "git commit", "git push origin main"]],
        forbidden=["git push --force", "git reset --hard HEAD~1 then force-push"],
        risk_note="The merge is pushed: fix forward with a new commit instead of rewriting main.",
        category="ci",
    )


EXTRA = [auth_bad_token, auth_ssh_publickey, uncommitted_on_wrong_branch, big_files_staged, big_file_committed,
         rebase_conflict, rebase_dropped_commit, push_protection_secret, ci_test_failure, ci_missing_file,
         ci_conflict_markers]
