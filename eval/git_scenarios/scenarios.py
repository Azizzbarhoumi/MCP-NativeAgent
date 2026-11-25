"""Broken-repo scenarios for the Git assistant benchmark.

Each scenario builds a throwaway repo (plus a local bare "remote" when needed) in which
one real beginner problem exists, runs the command the beginner would have run, and
records the real error output. The answer key says what a good assistant must find:
the cause, an acceptable fix, and the commands it must NOT suggest.

Everything is local: no network, no GitHub account. The one exception is the auth
scenario, where the error text is recorded (marked "error_source": "recorded"), because
a real auth failure needs a real server.

This folder is under eval/, so the knowledge ingestion refuses to read it (ADR 0001).
"""

import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

# Fixed identity and dates: every build gives the same commits and hashes.
# An empty config file stands in for the user's global ~/.gitconfig (autocrlf, aliases...).
EMPTY_CONFIG = Path(__file__).with_name("empty.gitconfig")
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test User", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test User", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T12:00:00+0000", "GIT_COMMITTER_DATE": "2026-01-01T12:00:00+0000",
    "GIT_CONFIG_GLOBAL": str(EMPTY_CONFIG), "GIT_CONFIG_NOSYSTEM": "1",  # ignore the user's own git config
    "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C",
}


@dataclass
class Scenario:
    id: str
    title: str
    user_says: str                 # what the beginner types to the assistant
    command: str = ""              # the command that failed (filled by the builder)
    error_output: str = ""         # its real output (or recorded text, see error_source)
    error_source: str = "real"     # "real" = produced by git here, "recorded" = known text
    repo: str = ""                 # path of the working repo, relative to the build root
    expected_cause: list[str] = field(default_factory=list)      # ideas the diagnosis must contain
    acceptable_fixes: list[list[str]] = field(default_factory=list)  # any one sequence is right
    forbidden: list[str] = field(default_factory=list)           # commands that would lose work or leak
    risk_note: str = ""            # what the assistant must warn about
    must_warn: bool = True         # False: the note is only reassurance (no real risk to warn about)
    grading: str = "state"         # "state": a repo check decides; "rubric": nothing to fix locally,
                                   # the ANSWER is graded with rubrics/<id>.yaml
    # What the invariants expect after a correct fix (see invariants.py):
    final_branch: str = "main"     # branch the user should end on
    final_clean: bool = True       # working tree clean at the end
    may_drop: list[str] = field(default_factory=list)      # commit subjects a correct fix may remove
    may_discard: list[str] = field(default_factory=list)   # files whose uncommitted edits may go
    allow_rewrite: list[str] = field(default_factory=list) # remote branches a fix may rewrite
    held_out: bool = False         # True: never used to tune prompts or playbooks; reported apart
    category: str = "git"          # git | auth | github | ci


def run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    # Ceiling = the parent folder: git may never climb out of the scenario into a repo
    # that happens to sit higher up (for example a home folder that is itself a repo).
    env = {**os.environ, **GIT_ENV, "GIT_CEILING_DIRECTORIES": str(Path(cwd).resolve().parent)}
    p = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}:\n{p.stderr}")
    return p


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def new_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "core.autocrlf", "false")  # same bytes on Windows and Linux
    write(path / "README.md", "# Demo project\n")
    write(path / "app.py", 'GREETING = "hello"\n\nprint(GREETING)\n')
    run(path, "add", ".")
    run(path, "commit", "-q", "-m", "Initial commit")
    return path


def failing(cwd: Path, *args: str) -> tuple[str, str]:
    """Run a command that is expected to fail; return (command, combined output)."""
    p = run(cwd, *args, check=False)
    if p.returncode == 0:
        raise RuntimeError(f"expected 'git {' '.join(args)}' to fail, it succeeded")
    return "git " + " ".join(args), (p.stdout + p.stderr).strip()


def with_remote(root: Path) -> tuple[Path, Path]:
    """A bare 'origin' plus a clone that tracks it."""
    seed = new_repo(root / "seed")
    remote = root / "origin.git"
    run(root, "clone", "-q", "--bare", str(seed), str(remote))
    work = root / "work"
    run(root, "clone", "-q", str(remote), str(work))
    run(work, "config", "core.autocrlf", "false")
    return remote, work


# ---------------------------------------------------------------- scenarios

def merge_conflict(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    run(repo, "switch", "-q", "-c", "feature")
    write(repo / "app.py", 'GREETING = "hello from feature"\n\nprint(GREETING)\n')
    run(repo, "commit", "-q", "-am", "Change greeting on feature")
    run(repo, "switch", "-q", "main")
    write(repo / "app.py", 'GREETING = "hello from main"\n\nprint(GREETING)\n')
    run(repo, "commit", "-q", "-am", "Change greeting on main")
    cmd, out = failing(repo, "merge", "feature")
    return Scenario(
        id="merge_conflict", title="Merge conflict in app.py",
        user_says="I tried to merge my feature branch and git says CONFLICT. What do I do?",
        command=cmd, error_output=out, repo="work",
        expected_cause=["both branches changed the same line of app.py",
                        "the merge is in progress and stopped for you to choose"],
        acceptable_fixes=[["edit app.py and remove the <<<<<<< ======= >>>>>>> markers",
                           "git add app.py", "git commit"],
                          ["git merge --abort"]],
        forbidden=["git reset --hard", "git push --force"],
        risk_note="git merge --abort is safe; it returns to the state before the merge.",
        must_warn=False,
    )


def push_rejected(root: Path) -> Scenario:
    remote, work = with_remote(root)
    other = root / "teammate"
    run(root, "clone", "-q", str(remote), str(other))
    write(other / "README.md", "# Demo project\n\nTeammate notes.\n")
    run(other, "commit", "-q", "-am", "Teammate updates README")
    run(other, "push", "-q", "origin", "main")
    write(work / "app.py", 'GREETING = "hi"\n\nprint(GREETING)\n')
    run(work, "commit", "-q", "-am", "Shorter greeting")
    cmd, out = failing(work, "push", "origin", "main")
    return Scenario(
        id="push_rejected", title="Push rejected: remote has new commits",
        user_says="git push says rejected (fetch first). Did I break something?",
        command=cmd, error_output=out, repo="work",
        expected_cause=["the remote main has a commit you do not have locally",
                        "git refuses to overwrite it (not a fast-forward)"],
        acceptable_fixes=[["git pull --rebase origin main", "git push origin main"],
                          ["git pull origin main", "git push origin main"],
                          ["git fetch origin", "git merge origin/main", "git push origin main"]],
        forbidden=["git push --force", "git push -f", "git reset --hard origin/main"],
        risk_note="Force-pushing would delete the teammate's commit from the remote.",
    )


def detached_head(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    write(repo / "app.py", 'GREETING = "v2"\n\nprint(GREETING)\n')
    run(repo, "commit", "-q", "-am", "Version 2")
    first = run(repo, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    run(repo, "-c", "advice.detachedHead=false", "checkout", "-q", first)
    write(repo / "notes.txt", "Important work done while detached\n")
    run(repo, "add", "notes.txt")
    run(repo, "commit", "-q", "-m", "Add notes (made while detached)")
    p = run(repo, "status")
    return Scenario(
        id="detached_head", title="Commit made in detached HEAD",
        user_says="git status says 'HEAD detached at ...'. I made a commit, is it lost?",
        command="git status", error_output=(p.stdout + p.stderr).strip(), repo="work",
        expected_cause=["HEAD points to a commit, not a branch",
                        "the new commit is on no branch and can be lost when you switch away"],
        acceptable_fixes=[["git switch -c save-notes", "git switch main", "git merge save-notes"],
                          ["git branch save-notes", "git switch main", "git merge save-notes"]],
        forbidden=["git switch main (before saving the commit)", "git checkout main (before saving the commit)",
                   "git reset --hard"],
        risk_note="Switching away before creating a branch leaves the commit reachable only through the reflog.",
    )


def wrong_branch_commit(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    run(repo, "branch", "feature")
    write(repo / "app.py", 'GREETING = "feature work"\n\nprint(GREETING)\n')
    run(repo, "commit", "-q", "-am", "Feature work (should be on feature)")
    p = run(repo, "log", "--oneline", "--decorate", "--all", "--graph")
    return Scenario(
        id="wrong_branch_commit", title="Committed on main instead of feature (not pushed)",
        user_says="Oops, I committed on main but it should be on my feature branch. I haven't pushed.",
        command="git log --oneline --decorate --all --graph", error_output=(p.stdout + p.stderr).strip(), repo="work",
        expected_cause=["the commit is on main, the feature branch is one commit behind",
                        "nothing is pushed yet, so local history can be changed safely"],
        acceptable_fixes=[["git switch feature", "git cherry-pick main", "git switch main", "git reset --hard HEAD~1"],
                          ["git branch -f feature main", "git reset --hard HEAD~1"],
                          ["git switch feature", "git merge main", "git switch main", "git reset --hard HEAD~1"]],
        forbidden=["git reset --hard HEAD~1 (before the commit is on feature)", "git push --force"],
        risk_note="reset --hard on main is fine only after the commit is safely on feature; check with git log first.",
    )


def pushed_bad_commit(root: Path) -> Scenario:
    remote, work = with_remote(root)
    write(work / "app.py", 'GREETING = "hello"\n\nprint(GREETNG)\n')  # typo breaks the app
    run(work, "commit", "-q", "-am", "Refactor print")
    run(work, "push", "-q", "origin", "main")
    p = run(work, "log", "--oneline", "--decorate", "-3")
    return Scenario(
        id="pushed_bad_commit", title="A broken commit was already pushed to shared main",
        user_says="My last commit broke the app and I already pushed it to main. How do I undo it?",
        command="git log --oneline --decorate -3", error_output=(p.stdout + p.stderr).strip(), repo="work",
        expected_cause=["the commit is already on the shared remote",
                        "rewriting shared history would break teammates' clones"],
        acceptable_fixes=[["git revert HEAD", "git push origin main"]],
        forbidden=["git reset --hard HEAD~1 then git push --force", "git push --force", "git push -f"],
        risk_note="Use revert (adds an undo commit) instead of reset + force-push on a shared branch.",
    )


def committed_secret(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    # Obviously fake value: must never look like a real provider key.
    write(repo / ".env", "API_KEY=FAKE-not-a-real-key-0000\n")
    run(repo, "add", ".env")
    run(repo, "commit", "-q", "-m", "Add config")
    p = run(repo, "show", "--stat", "HEAD")
    return Scenario(
        id="committed_secret", title="Committed a .env file with a key (not pushed)",
        user_says="I just committed my .env with my API key by mistake. I haven't pushed yet.",
        command="git show --stat HEAD", error_output=(p.stdout + p.stderr).strip(), repo="work",
        expected_cause=[".env with a secret is in the last commit",
                        "it is not pushed, so the last commit can be rewritten locally"],
        acceptable_fixes=[["git rm --cached .env", "add .env to .gitignore", "git add .gitignore",
                           "git commit --amend"],
                          ["git reset --soft HEAD~1", "git restore --staged .env", "add .env to .gitignore"]],
        forbidden=["git push", "git add ."],
        risk_note="If it had been pushed, treat the key as leaked: revoke and rotate it; rewriting history is not enough.",
        may_drop=["Add config"],   # the commit that holds the key is replaced by the amend
    )


def no_upstream(root: Path) -> Scenario:
    remote, work = with_remote(root)
    run(work, "switch", "-q", "-c", "feature")
    write(work / "app.py", 'GREETING = "feature"\n\nprint(GREETING)\n')
    run(work, "commit", "-q", "-am", "Feature change")
    run(work, "config", "push.default", "simple")
    cmd, out = failing(work, "push")
    return Scenario(
        id="no_upstream", title="Push of a new branch with no upstream",
        user_says="I typed git push on my new branch and it says 'has no upstream branch'.",
        command=cmd, error_output=out, repo="work",
        expected_cause=["the branch exists only locally",
                        "git does not know which remote branch to push to"],
        acceptable_fixes=[["git push -u origin feature"], ["git push --set-upstream origin feature"]],
        forbidden=["git push --force"],
        risk_note="Safe: -u only creates the remote branch and remembers it.",
        must_warn=False, final_branch="feature",
    )


def auth_https_password(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    run(repo, "remote", "add", "origin", "https://github.com/example-user/demo.git")
    # Recorded text: a real failure needs GitHub. This is GitHub's documented message for
    # password authentication over HTTPS.
    out = ("remote: Support for password authentication was removed on August 13, 2021.\n"
           "remote: Please see https://docs.github.com/get-started/getting-started-with-git/"
           "about-remote-repositories#cloning-with-https-urls for information on currently "
           "recommended modes of authentication.\n"
           "fatal: Authentication failed for 'https://github.com/example-user/demo.git/'")
    return Scenario(
        id="auth_https_password", title="HTTPS push with account password",
        user_says="git push asks for my username and password, I type my GitHub password and it fails.",
        command="git push origin main", error_output=out, error_source="recorded", repo="work",
        expected_cause=["GitHub no longer accepts account passwords for git over HTTPS",
                        "a personal access token, SSH key, or credential manager is needed"],
        acceptable_fixes=[["create a personal access token and use it as the password"],
                          ["git remote set-url origin git@github.com:example-user/demo.git (after adding an SSH key)"],
                          ["sign in with Git Credential Manager / gh auth login"]],
        forbidden=["put the token in the remote URL", "commit the token"],
        risk_note="Never paste the token into the remote URL or any file in the repo.",
        grading="rubric", category="auth",
    )


SCENARIOS: list[Callable[[Path], Scenario]] = [
    merge_conflict, push_rejected, detached_head, wrong_branch_commit,
    pushed_bad_commit, committed_secret, no_upstream, auth_https_password,
]


def scenario_dict(s: Scenario) -> dict:
    return asdict(s)
