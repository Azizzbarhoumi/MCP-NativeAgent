"""The one risk table for git steps. What the assistant shows the user (risk.py) and what the
tool enforces (execute.py) both come from here, so the two can no longer disagree.

Tiers, and the approval each one needs before the tool runs it:
  safe         easy to undo, nothing is lost            one click (no code)
  careful      changes history or publishes, but        the approval code
               everything can be got back
  destructive  can lose work or hurt teammates          the approval code + the typed phrase,
                                                        and a backup is made first
  blocked      never run by the tool (credentials, git config, whole-history rewrites)

A step's tier is its base tier from TABLE, raised by the repo's state (decide()). The rules only
ever RAISE a tier: a state can make a step more dangerous, never less.

Why `git pull --rebase` is careful, and when it is destructive (settled 2026-11-26):
  It downloads the remote branch, then replays YOUR commits on top of it, giving them new ids.
  - If those commits exist only on your computer, nothing is lost: the old ids are still in the
    reflog, the tool makes a backup ref first, and `git rebase --abort` undoes a stop halfway.
    Nobody else has the old ids, so nobody's history breaks. That is "careful", the same tier as
    a merge, which is the other way to settle a rejected push.
  - If any commit it would rewrite is already on a remote, the new ids no longer match what
    others have: pushing then needs a force-push, which can delete teammates' work. That is
    "destructive", and the same rule covers `git rebase`, `--amend` and `reset` of pushed commits.
"""

import re
from pathlib import Path

TIERS = ("safe", "careful", "destructive")

# key: an operation of execute.OPS, or a kind of command the tool does not run (marked "no op").
TABLE: dict[str, tuple[str, str]] = {
    # ---- safe: easy to undo, nothing lost
    "read": ("safe", "read-only: looks, changes nothing"),
    "fetch": ("safe", "only downloads from the remote; your branches don't move"),
    "create_branch": ("safe", "adds a new branch name; nothing moves"),
    "switch_create": ("safe", "makes a branch where you are and moves onto it; nothing is lost"),
    "switch": ("safe", "moves to another branch; your commits stay where they are"),
    "add": ("safe", "stages files; undo with git restore --staged"),
    "rm_cached": ("safe", "only stops tracking the file; the file stays on disk"),
    "restore_staged": ("safe", "unstages files; your edits stay"),
    "gitignore_add": ("safe", "adds one line to .gitignore"),
    "commit": ("safe", "records a new commit; undo with git reset --soft HEAD~1"),
    "merge_commit": ("safe", "finishes the merge you already resolved"),
    "stash": ("safe", "puts changes aside; git stash pop brings them back"),
    "stash_pop": ("safe", "brings stashed changes back; on a conflict the stash is kept"),
    "merge_abort": ("safe", "returns to the state before the merge"),
    "rebase_abort": ("safe", "returns to the state before the rebase"),
    "abort": ("safe", "returns to the state before the operation"),                      # no op
    "branch_delete_merged": ("safe", "git refuses if the branch has unmerged commits"),  # no op
    # ---- careful: changes history or publishes, recoverable
    "resolve_conflict": ("careful", "replaces a conflicted file with one side; both sides stay in their "
                                    "commits, but hand edits to that file are dropped"),
    "merge": ("careful", "joins another branch into this one; undo with git merge --abort or a reset"),
    "pull": ("careful", "downloads and merges the remote branch into yours"),
    "rebase": ("careful", "replays your local commits with new ids; a backup is made and "
                          "git rebase --abort undoes it"),
    "rebase_continue": ("careful", "goes on with the rebase after the conflicts are resolved"),
    "revert": ("careful", "adds a commit that undoes an old one; history is kept"),
    "cherry_pick": ("careful", "copies a commit onto this branch"),
    "reset_soft": ("careful", "moves the branch back; the changes stay staged"),
    "reset_mixed": ("careful", "moves the branch back; the changes stay in your files"),   # no op
    "amend": ("careful", "replaces the last commit; don't amend pushed commits"),
    "push": ("careful", "publishes your commits to the remote"),
    "rm": ("careful", "deletes the file from disk and from the next commit"),             # no op
    "config": ("careful", "changes git settings (check --global vs this repo)"),          # blocked
    "outside": ("careful", "not a git command; review it yourself"),                      # no op
    "unknown": ("careful", "unknown git command"),                                         # no op
    # ---- destructive: can lose work or hurt others
    "reset_hard": ("destructive", "throws away uncommitted changes and moves the branch"),
    "push_force": ("destructive", "force-push rewrites the remote branch and can delete teammates' commits"),
    "clean": ("destructive", "deletes untracked files permanently"),
    "delete_branch": ("destructive", "deletes a branch even if its commits are not merged"),
    "discard_changes": ("destructive", "overwrites local file changes with no undo"),     # no op
    "stash_drop": ("destructive", "deletes saved stashes"),                                # no op
    "filter_branch": ("destructive", "rewrites the whole history"),                        # blocked
    "rm_files": ("destructive", "deletes files outside git's undo"),                       # no op
    "credential": ("destructive", "the command contains a credential; it would be stored in shell history"),
}

PUSHED = "the commits it rewrites are already on a remote: rewriting them needs a force-push"


def rank(tier: str) -> int:
    return TIERS.index(tier)


def worst(tiers) -> str:
    tiers = list(tiers)
    return max(tiers, key=rank) if tiers else "safe"


def decide(key: str, facts: dict | None = None) -> tuple[str, str]:
    """(tier, reason) for one step. facts (all optional, missing = not known):
      detached          HEAD is detached
      detached_commits  commits on the detached HEAD that are on no branch, tag or remote
      leaves_head       the step moves HEAD away (switch without creating a branch)
      rewrites_pushed   the step rewrites at least one commit that is already on a remote
      in_progress       'merge', 'rebase'... when an operation is half done
    """
    tier, reason = TABLE.get(key, TABLE["unknown"])
    f = facts or {}

    def raise_to(t: str, why: str):
        nonlocal tier, reason
        if rank(t) > rank(tier):
            tier, reason = t, why

    if key == "switch" and f.get("detached") and f.get("detached_commits"):
        raise_to("destructive", "HEAD is detached: commits made here are on no branch and are left "
                                "behind when you switch; create a branch first")
    if key in ("rebase", "amend", "reset_soft", "reset_mixed") and f.get("rewrites_pushed"):
        raise_to("destructive", PUSHED)
    if f.get("in_progress") and key in ("switch", "pull", "rebase", "merge"):
        raise_to("careful", f"a {f['in_progress']} is in progress; finish or abort it first")
    return tier, reason


# ------------------------------------------------------------ facts read from a real repo

def _git(repo: Path, *args: str) -> str:
    from mcp_servers.git_local import safe_git
    return safe_git.run(repo, list(args), read_only=True, ok_codes=(0, 1, 128, 129)).stdout


def _on_remote(repo: Path, rev_range: list[str]) -> bool:
    """True if any commit in the range is contained in a remote-tracking branch."""
    mine = _git(repo, "rev-list", *rev_range).split()
    if not mine:
        return False
    local_only = set(_git(repo, "rev-list", *rev_range, "--not", "--remotes").split())
    return any(c not in local_only for c in mine)


def repo_facts(repo: Path, key: str, params: dict | None = None) -> dict:
    """The facts decide() needs, read from the repo (read-only git). Only what `key` uses."""
    params = params or {}
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    facts: dict = {"detached": not branch, "in_progress": _in_progress(repo)}
    if key == "switch" and not branch:
        facts["detached_commits"] = len(_git(repo, "rev-list", "HEAD", "--not", "--branches", "--tags",
                                             "--remotes").split())
    if key == "amend":
        facts["rewrites_pushed"] = _on_remote(repo, ["-1", "HEAD"])
    if key in ("reset_soft", "reset_mixed"):
        rev = params.get("rev", "HEAD~1")
        ok = isinstance(rev, str) and re.fullmatch(r"(?!-)[\w./~^@{}-]{1,80}", rev)
        facts["rewrites_pushed"] = _on_remote(repo, [f"{rev}..HEAD"] if ok else ["-1", "HEAD"])
    if key == "rebase" and params.get("onto") and re.fullmatch(r"(?!-)[\w./-]{1,100}", params["onto"]):
        facts["rewrites_pushed"] = _on_remote(repo, [f"{params['onto']}..HEAD"])
    return facts


def _in_progress(repo: Path) -> str | None:
    git_dir = repo / ".git"
    for marker, name in [("MERGE_HEAD", "merge"), ("rebase-merge", "rebase"), ("rebase-apply", "rebase"),
                         ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert")]:
        if (git_dir / marker).exists():
            return name
    return None
