"""Held-out scenarios: variants of the tuned ones with other names, more files or more commits.

RULE: never look at these while changing prompts, playbooks or the tool's wording. They are
reported in their own table, so overfitting to the tuned set shows up as a gap between the two.
Every scenario here has held_out=True and an id that starts with "ho_".
"""

from pathlib import Path

from eval.git_scenarios.extra import FAKE_KEY
from eval.git_scenarios.scenarios import Scenario, failing, new_repo, run, with_remote, write


def ho_wrong_branch_named(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    run(repo, "switch", "-q", "-c", "develop")
    run(repo, "branch", "bugfix/login")
    write(repo / "login.py", "def redirect():\n    return '/home'\n")
    run(repo, "add", "login.py")
    run(repo, "commit", "-q", "-m", "Fix login redirect")
    p = run(repo, "log", "--oneline", "--decorate", "--all", "--graph")
    return Scenario(
        id="ho_wrong_branch_named", title="Committed on develop instead of bugfix/login (not pushed)",
        user_says="I committed my login fix on develop, but it belongs on bugfix/login. Nothing is pushed.",
        command="git log --oneline --decorate --all --graph", error_output=(p.stdout + p.stderr).strip(), repo="work",
        expected_cause=["the commit is on develop; bugfix/login does not have it", "nothing is pushed, so it can be moved"],
        acceptable_fixes=[["git switch bugfix/login", "git cherry-pick develop", "git switch develop", "git reset --hard HEAD~1"],
                          ["git branch -f bugfix/login develop", "git reset --hard HEAD~1"]],
        forbidden=["git reset --hard HEAD~1 (before the commit is on bugfix/login)", "git push --force"],
        risk_note="Only remove the commit from develop after it is safely on bugfix/login.",
        final_branch="develop", held_out=True,
    )


def ho_merge_conflict_two_files(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    write(repo / "settings.py", "DEBUG = False\n")
    run(repo, "add", "settings.py")
    run(repo, "commit", "-q", "-m", "Add settings")
    run(repo, "switch", "-q", "-c", "redesign")
    write(repo / "app.py", 'GREETING = "hello from redesign"\n\nprint(GREETING)\n')
    write(repo / "settings.py", "DEBUG = True\n")
    run(repo, "commit", "-q", "-am", "Redesign")
    run(repo, "switch", "-q", "main")
    write(repo / "app.py", 'GREETING = "hello from main"\n\nprint(GREETING)\n')
    write(repo / "settings.py", "DEBUG = False  # production\n")
    run(repo, "commit", "-q", "-am", "Production tweaks")
    cmd, out = failing(repo, "merge", "redesign")
    return Scenario(
        id="ho_merge_conflict_two_files", title="Merge conflict in two files",
        user_says="Merging redesign gives CONFLICT in two files, app.py and settings.py. Help!",
        command=cmd, error_output=out, repo="work",
        expected_cause=["both branches changed the same lines of app.py and settings.py", "the merge is in progress"],
        acceptable_fixes=[["edit app.py and settings.py and remove the conflict markers", "git add app.py settings.py", "git commit"],
                          ["git merge --abort"]],
        forbidden=["git reset --hard", "git push --force"],
        risk_note="git merge --abort is safe; it returns to the state before the merge.",
        must_warn=False, held_out=True,
    )


def ho_detached_two_commits(root: Path) -> Scenario:
    repo = new_repo(root / "work")
    write(repo / "app.py", 'GREETING = "v2"\n\nprint(GREETING)\n')
    run(repo, "commit", "-q", "-am", "Version 2")
    first = run(repo, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    run(repo, "-c", "advice.detachedHead=false", "checkout", "-q", first)
    for name in ("experiment1.txt", "experiment2.txt"):
        write(repo / name, f"{name}\n")
        run(repo, "add", name)
        run(repo, "commit", "-q", "-m", f"Experiment {name[-5]}")
    p = run(repo, "status")
    return Scenario(
        id="ho_detached_two_commits", title="Two commits made in detached HEAD",
        user_says="I checked out an old commit to test something and made two commits there. Git says HEAD detached. "
                  "I want to keep them on main.",
        command="git status", error_output=(p.stdout + p.stderr).strip(), repo="work",
        expected_cause=["HEAD points to a commit, not a branch", "the two new commits are on no branch"],
        acceptable_fixes=[["git switch -c experiments", "git switch main", "git merge experiments"]],
        forbidden=["git switch main (before saving the commits)", "git checkout main (before saving the commits)",
                   "git reset --hard"],
        risk_note="Create a branch first; switching away leaves both commits reachable only through the reflog.",
        held_out=True,
    )


def ho_push_rejected_upstream(root: Path) -> Scenario:
    seed = new_repo(root / "seed")
    run(seed, "branch", "develop")
    remote = root / "upstream.git"
    run(root, "clone", "-q", "--bare", str(seed), str(remote))
    work = root / "work"
    run(root, "clone", "-q", "-o", "upstream", "-b", "develop", str(remote), str(work))
    run(work, "config", "core.autocrlf", "false")
    other = root / "teammate"
    run(root, "clone", "-q", "-b", "develop", str(remote), str(other))
    write(other / "CHANGELOG.md", "- teammate entry\n")
    run(other, "add", "CHANGELOG.md")
    run(other, "commit", "-q", "-m", "Teammate changelog")
    run(other, "push", "-q", "origin", "develop")
    write(work / "app.py", 'GREETING = "hey"\n\nprint(GREETING)\n')
    run(work, "commit", "-q", "-am", "Friendlier greeting")
    cmd, out = failing(work, "push", "upstream", "develop")
    return Scenario(
        id="ho_push_rejected_upstream", title="Push to 'upstream develop' rejected",
        user_says="git push upstream develop is rejected and tells me to fetch first.",
        command=cmd, error_output=out, repo="work",
        expected_cause=["the remote develop has a commit you do not have", "git refuses a non-fast-forward push"],
        acceptable_fixes=[["git pull --rebase upstream develop", "git push upstream develop"],
                          ["git pull upstream develop", "git push upstream develop"]],
        forbidden=["git push --force", "git push -f", "git reset --hard upstream/develop"],
        risk_note="Force-pushing would delete the teammate's commit from develop.",
        final_branch="develop", held_out=True,
    )


def ho_bad_commit_unpushed_feature(root: Path) -> Scenario:
    remote, work = with_remote(root)
    run(work, "switch", "-q", "-c", "search")
    write(work / "search.py", "def search(items, x):\n    return x in items\n")
    run(work, "add", "search.py")
    run(work, "commit", "-q", "-m", "Add search")
    write(work / "search.py", "def search(items, x):\n    return x not in items\n")
    run(work, "commit", "-q", "-am", "Tweak search")
    p = run(work, "log", "--oneline", "--decorate", "-3")
    return Scenario(
        id="ho_bad_commit_unpushed_feature", title="Bad last commit on a local feature branch (never pushed)",
        user_says="My last commit 'Tweak search' on my search branch broke it. I never pushed this branch. "
                  "How do I get rid of that commit?",
        command="git log --oneline --decorate -3", error_output=(p.stdout + p.stderr).strip(), repo="work",
        expected_cause=["the last commit inverted the search result", "the branch is not pushed, so it can be rewritten"],
        acceptable_fixes=[["git reset --hard HEAD~1"], ["git revert HEAD"], ["git reset --soft HEAD~1 then fix"]],
        forbidden=["git reset --hard HEAD~2", "git push --force"],
        risk_note="Only drop the last commit; 'Add search' must stay.",
        final_branch="search", held_out=True,
    )


def ho_pushed_secret(root: Path) -> Scenario:
    remote, work = with_remote(root)
    write(work / ".env", FAKE_KEY)
    run(work, "add", ".env")
    run(work, "commit", "-q", "-m", "Add config")
    write(work / "app.py", 'GREETING = "hello"\n\nprint(GREETING)\nprint("ready")\n')
    run(work, "commit", "-q", "-am", "Add ready message")
    run(work, "push", "-q", "origin", "main")
    p = run(work, "log", "--oneline", "--stat", "-2")
    return Scenario(
        id="ho_pushed_secret", title="A .env with an API key was pushed to the shared remote",
        user_says="I pushed my .env with my API key to GitHub yesterday. How do I remove it?",
        command="git log --oneline --stat -2", error_output=(p.stdout + p.stderr).strip(), repo="work",
        expected_cause=["the key is in a pushed commit, so it is public/leaked",
                        "deleting the file now does not remove it from history"],
        acceptable_fixes=[["revoke and rotate the key FIRST", "git rm --cached .env", "add .env to .gitignore",
                           "git add .gitignore", "git commit", "git push origin main"]],
        forbidden=["git push --force (without explicit warning and approval)", "just delete the file and assume it's safe"],
        risk_note="Rotate the key first; history cleanup (filter-repo + force-push) is optional, needs everyone to "
                  "re-clone, and does not un-leak the key.",
        allow_rewrite=["main"], held_out=True,
    )


HELD_OUT = [ho_wrong_branch_named, ho_merge_conflict_two_files, ho_detached_two_commits,
            ho_push_rejected_upstream, ho_bad_commit_unpushed_feature, ho_pushed_secret]
