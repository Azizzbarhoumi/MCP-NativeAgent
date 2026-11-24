"""Risk tiers for git commands the assistant proposes.

  safe         reads, or changes that are easy to undo (switch, branch, add, commit, stash)
  careful      changes history or the working tree but recoverable (rebase, reset --soft,
               amend, pull, merge, cherry-pick, revert, push without force)
  destructive  can lose work or hurt others (reset --hard, clean -f, push --force,
               branch -D, checkout -- / restore on modified files, filter-branch)

Rules are checked top to bottom; the first match wins, so the dangerous patterns come
first. Unknown commands are "careful" with "unknown" as the reason: never silently safe.
"""

import re
import shlex

RULES: list[tuple[str, str, str]] = [
    # (regex on the normalized command, tier, reason)
    (r"^git push\b.*(--force\b|-f\b|--force-with-lease|\+\S+)", "destructive",
     "force-push rewrites the remote branch and can delete teammates' commits"),
    (r"^git reset\b.*--hard", "destructive", "throws away uncommitted changes and moves the branch"),
    (r"^git clean\b.*-\w*f", "destructive", "deletes untracked files permanently"),
    (r"^git branch\b.*(-D\b|--delete --force)", "destructive", "deletes a branch even if not merged"),
    (r"^git (checkout|restore)\b.*(\s--\s|\s\.$|--worktree)", "destructive",
     "overwrites local file changes with no undo"),
    (r"^git restore\b(?!.*--staged)", "destructive", "discards local file changes with no undo"),
    (r"^git (filter-branch|filter-repo)\b", "destructive", "rewrites the whole history"),
    (r"^git stash (drop|clear)\b", "destructive", "deletes saved stashes"),
    (r"^git (merge|rebase|cherry-pick|revert) --abort\b", "safe", "returns to the state before the operation"),
    (r"^git commit\b.*--amend", "careful", "rewrites the last commit; don't amend pushed commits"),
    (r"^git (rebase|reset|cherry-pick|revert|merge|pull)\b", "careful",
     "changes history or merges; check the result with git log"),
    (r"^git push\b", "careful", "publishes commits to the remote"),
    (r"^git rm\b.*--cached", "safe", "only stops tracking the file; the file stays on disk"),
    (r"^git rm\b", "careful", "deletes the file from disk and from the next commit"),
    (r"^git config\b", "careful", "changes git settings (check --global vs this repo)"),
    (r"^git (status|log|diff|show|reflog|branch|remote -v|fetch|stash list|blame|ls-files|rev-parse)\b",
     "safe", "read-only, or only downloads from the remote"),
    (r"^git (switch|checkout|branch|add|commit|stash|restore --staged|remote add|tag|merge --abort|rebase --abort)\b",
     "safe", "easy to undo"),
]

SECRET_IN_COMMAND = re.compile(r"(gh[pousr]_\w{20,}|github_pat_\w{20,}|://[^/\s:@]+:[^/\s@]+@)")


def normalize(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return " ".join(parts)


def classify(command: str) -> dict:
    cmd = normalize(command)
    if SECRET_IN_COMMAND.search(cmd):
        return {"command": "<redacted>", "tier": "destructive",
                "reason": "the command contains a credential; it would be stored in shell history"}
    if re.match(r"^(rm|del|rmdir|Remove-Item)\b", cmd):
        return {"command": cmd, "tier": "destructive", "reason": "deletes files outside git's undo"}
    if not cmd.startswith("git "):
        return {"command": cmd, "tier": "careful", "reason": "not a git command; review it yourself"}
    for pattern, tier, reason in RULES:
        if re.search(pattern, cmd):
            return {"command": cmd, "tier": tier, "reason": reason}
    return {"command": cmd, "tier": "careful", "reason": "unknown git command"}


TIERS = ["safe", "careful", "destructive"]


def after(command: str, state: dict | None) -> dict | None:
    """Rough effect of a step on the state, so the NEXT step is rated in the right context
    (after `git switch -c save`, HEAD is no longer detached)."""
    if not state or not state.get("is_repo"):
        return state
    cmd, s = normalize(command), dict(state)
    m = re.match(r"^git (switch|checkout) (?:(-c|-b|-C|-B|--create) )?([\w./-]+)$", cmd)
    if m and not m.group(3).startswith("-"):
        s.update(detached=False, branch=m.group(3))
    if re.match(r"^git (merge|rebase|cherry-pick|revert) --abort\b", cmd) or (
            s.get("in_progress") and re.match(r"^git (commit|merge --continue|rebase --continue)\b", cmd)):
        s["in_progress"] = None
    return s


def in_context(command: str, state: dict | None) -> dict:
    """classify() plus what the repo state changes. A command that is harmless in general can
    lose work in a specific repo, e.g. switching away from a detached HEAD with a new commit."""
    r = classify(command)
    if not state or not state.get("is_repo"):
        return r
    cmd = r["command"]

    def raise_to(tier: str, reason: str):
        if TIERS.index(tier) > TIERS.index(r["tier"]):
            r.update(tier=tier, reason=reason)

    leaves_head = re.match(r"^git (switch|checkout)\b(?!.*\s(-c|-b|-C|-B|--create)\b)", cmd)
    if state.get("detached") and leaves_head:
        raise_to("destructive", "HEAD is detached: commits made here are on no branch and are "
                                "left behind when you switch; create a branch first")
    if state.get("in_progress") and re.match(r"^git (switch|checkout|pull|rebase|merge)\b(?!.*--abort)", cmd):
        raise_to("careful", f"a {state['in_progress']} is in progress; finish or abort it first")
    if state.get("upstream") and re.match(r"^git (commit --amend|rebase|reset)\b", cmd) and state.get("ahead", 0) == 0:
        raise_to("destructive", "the current commit is already pushed; rewriting it needs a force-push")
    return r
