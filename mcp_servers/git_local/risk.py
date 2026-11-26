"""Risk tier of a git command as TEXT, for the plan the assistant shows the user.

The tiers themselves live in tiers.py, the one table the executing tool uses too. This module
only recognises which kind of step a command is (its key in tiers.TABLE) and which repo facts
apply. tests/test_risk_single_table.py fails if this rating and the tool's ever disagree.

Rules are checked top to bottom; the first match wins, so the dangerous patterns come first.
Unknown commands are "careful" with "unknown" as the reason: never silently safe.
"""

import re
import shlex

from mcp_servers.git_local import tiers as T

RULES: list[tuple[str, str]] = [
    # (regex on the normalized command, key in tiers.TABLE)
    (r"^git push\b.*(--force\b|-f\b|--force-with-lease|\s\+\S+)", "push_force"),
    (r"^git reset\b.*--hard", "reset_hard"),
    (r"^git clean\b.*(-n\b|--dry-run)", "read"),
    (r"^git clean\b.*-\w*f", "clean"),
    (r"^git branch\b.*(-D\b|--delete --force|--force --delete)", "delete_branch"),
    (r"^git branch\b.*(\s-d\b|--delete)", "branch_delete_merged"),
    (r"^git checkout\b.*\s--(ours|theirs)\b", "resolve_conflict"),
    (r"^git (checkout|restore)\b.*(\s--\s|\s\.$|--worktree)", "discard_changes"),
    (r"^git restore\b(?!.*--staged)", "discard_changes"),
    (r"^git (filter-branch|filter-repo)\b", "filter_branch"),
    (r"^git stash (drop|clear)\b", "stash_drop"),
    (r"^git stash pop\b", "stash_pop"),
    (r"^git stash (list|show)\b", "read"),
    (r"^git stash( push| save)?\b", "stash"),
    (r"^git merge --abort\b", "merge_abort"),
    (r"^git rebase --abort\b", "rebase_abort"),
    (r"^git (cherry-pick|revert) --abort\b", "abort"),
    (r"^git merge --continue\b", "merge_commit"),
    (r"^git rebase --continue\b", "rebase_continue"),
    (r"^git commit\b.*--amend", "amend"),
    (r"^git commit\b", "commit"),
    (r"^git pull\b.*(--rebase\b|\s-r\b)", "rebase"),
    (r"^git pull\b", "pull"),
    (r"^git rebase\b", "rebase"),
    (r"^git merge\b", "merge"),
    (r"^git cherry-pick\b", "cherry_pick"),
    (r"^git revert\b", "revert"),
    (r"^git reset\b.*--soft", "reset_soft"),
    (r"^git reset( HEAD)? -- ", "restore_staged"),
    (r"^git reset\b", "reset_mixed"),
    (r"^git push\b", "push"),
    (r"^git fetch\b", "fetch"),
    (r"^git rm\b.*--cached", "rm_cached"),
    (r"^git rm\b", "rm"),
    (r"^git config\b", "config"),
    (r"^git restore --staged\b", "restore_staged"),
    (r"^git add\b", "add"),
    (r"^git (switch|checkout)\b.*\s(-c|-b|-C|-B|--create)\s", "switch_create"),
    (r"^git (switch|checkout)\b", "switch"),
    (r"^git (status|log|diff|show|reflog|remote -v|remote show|stash list|blame|ls-files|rev-parse|"
     r"check-ignore|shortlog|describe|grep|ls-remote)\b", "read"),
    (r"^git remote$", "read"),
    (r"^git branch\b.*(\s-a\b|\s-v\b|\s-vv\b|--list|\s-r\b|--all|--show-current)", "read"),
    (r"^git branch$", "read"),
    (r"^git branch\b", "create_branch"),
]

# `echo node_modules/ >> .gitignore`: the tool turns this into its gitignore_add step.
ECHO_GITIGNORE = re.compile(r"""^echo\s+['"]?[\w./*!-]+['"]?\s*>>\s*\.gitignore$""", re.I)

SECRET_IN_COMMAND = re.compile(r"(gh[pousr]_\w{20,}|github_pat_\w{20,}|://[^/\s:@]+:[^/\s@]+@)")


def normalize(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return " ".join(parts)


def key_of(command: str) -> str:
    """Which kind of step a command is: its key in tiers.TABLE."""
    cmd = normalize(command)
    if SECRET_IN_COMMAND.search(cmd):
        return "credential"
    if re.match(r"^(rm|del|rmdir|Remove-Item)\b", cmd):
        return "rm_files"
    if ECHO_GITIGNORE.match(cmd):
        return "gitignore_add"
    if not cmd.startswith("git "):
        return "outside"
    for pattern, key in RULES:
        if re.search(pattern, cmd):
            return key
    return "unknown"


def classify(command: str) -> dict:
    """The tier with no repo state known: the base tier from the one table."""
    key = key_of(command)
    tier, reason = T.decide(key)
    return {"command": "<redacted>" if key == "credential" else normalize(command), "key": key,
            "tier": tier, "reason": reason}


TIERS = list(T.TIERS)


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


def _rebase_onto(cmd: str, state: dict) -> str | None:
    """What a rebase written as text replays onto: `git rebase X` -> X;
    `git pull --rebase [remote [branch]]` -> remote/branch (defaults: the upstream)."""
    try:
        words = [w for w in shlex.split(cmd)[2:] if not w.startswith("-")]
    except ValueError:
        return None
    if cmd.startswith("git rebase"):
        return words[0] if words else state.get("upstream")
    if len(words) >= 2:
        return f"{words[0]}/{words[1]}"
    if len(words) == 1 and state.get("branch"):
        return f"{words[0]}/{state['branch']}"
    return state.get("upstream")


def _reset_rev(cmd: str) -> str:
    words = [w for w in cmd.split()[2:] if not w.startswith("-")]
    return words[0] if words else "HEAD"


def in_context(command: str, state: dict | None) -> dict:
    """classify() plus what the repo state changes, with the same rules the tool applies
    (tiers.decide). A command that is harmless in general can lose work in a specific repo,
    e.g. switching away from a detached HEAD with a new commit.

    With the repo's root in `state` (inspect_repo.repo_state), the facts are read from the repo
    exactly as the tool reads them. `state` wins for what earlier steps of the plan changed
    (after(): a `git switch -c` before means HEAD is no longer detached)."""
    r = classify(command)
    if not state or not state.get("is_repo"):
        return r
    key, cmd = r["key"], normalize(command)
    facts = {"detached": state.get("detached"), "in_progress": state.get("in_progress")}
    root = state.get("root")
    if root and key in ("switch", "rebase", "amend", "reset_soft", "reset_mixed"):
        from pathlib import Path
        params = {"onto": _rebase_onto(cmd, state)} if key == "rebase" else \
                 {"rev": _reset_rev(cmd)} if key.startswith("reset") else {}
        real = T.repo_facts(Path(root), key, params)
        facts.update({k: v for k, v in real.items() if k not in ("detached", "in_progress")})
    elif key == "switch":
        facts["detached_commits"] = 1 if state.get("detached") else 0      # not known: assume the worst
    elif key in ("rebase", "amend", "reset_soft", "reset_mixed"):
        facts["rewrites_pushed"] = bool(state.get("upstream")) and state.get("ahead", 0) == 0
    tier, reason = T.decide(key, facts)
    return r | {"tier": tier, "reason": reason}
