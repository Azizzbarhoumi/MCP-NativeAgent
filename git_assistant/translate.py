"""Turn a proposed step (a git command as text, or a manual instruction) into typed tool
operations, so the plan can run through prepare/execute. Deterministic: no LLM here.

    to_ops(step, repo, manual_files) -> list of Action

Action.kind:
  "op"       a typed operation for the tool: (op, params)
  "manual"   an edit the user does by hand (the benchmark may simulate it, see eval/.../manual.py)
  "read"     a read-only git command (status, log, diff...): nothing to run, nothing changes
  "outside"  not a git command (ssh-keygen, gh auth login, creating a token on GitHub): the
             user does it outside the tool; it cannot change this repo
  "unsupported"  a git command with no typed operation (on purpose: e.g. discarding edits
             with checkout -- / restore, or a mixed reset). Never run.

Blocked commands (config, remote set-url, filter-repo, gc) are passed to the tool as their op
name, so the tool's own refusal is recorded.
"""

import re
import shlex
from dataclasses import dataclass, field

from mcp_servers.git_local import safe_git


@dataclass
class Action:
    kind: str
    op: str = ""
    params: dict = field(default_factory=dict)
    text: str = ""
    note: str = ""


READ_ONLY = {"status", "log", "diff", "show", "reflog", "rev-parse", "ls-files", "check-ignore", "blame",
             "shortlog", "describe", "grep", "ls-remote", "whatchanged"}
BLOCKED = {"config": "config", "filter-branch": "filter_branch", "filter-repo": "filter_repo", "gc": "gc_prune",
           "update-ref": "update_ref", "credential": "credential"}
EDIT_VERB = re.compile(r"\b(edit|open|remove|delete the|fix|change|replace|resolve|keep|choose|move|rewrite|update)\b", re.I)
# "add `node_modules/` and `build/` to .gitignore", "put .env in your .gitignore"
GITIGNORE = re.compile(r"""(?:add|put|append|list|write)\s+(.+?)\s+(?:to|in|into)\s+(?:the\s+|a\s+|your\s+)?[`'"]?\.gitignore""", re.I)
PATTERN = re.compile(r"^[\w./*!-]+$")
ECHO_GITIGNORE = re.compile(r"""^(?:echo|Add-Content(?:\s+-Path)?\s+\.gitignore(?:\s+-Value)?)\s+['"]?([\w./*!-]+)['"]?\s*(?:>>\s*\.gitignore)?\s*$""", re.I)


def _git(repo, *args) -> str:
    return safe_git.run(repo, list(args), read_only=True, ok_codes=(0, 1, 128)).stdout.strip()


def _current_branch(repo) -> str:
    return _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")


def _upstream_remote(repo) -> str:
    up = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if "/" in up:
        return up.split("/", 1)[0]
    remotes = _git(repo, "remote").split()
    return "origin" if "origin" in remotes or not remotes else remotes[0]


def _in_merge(repo) -> bool:
    return (repo / ".git" / "MERGE_HEAD").exists()


def split_commands(command: str) -> list[str]:
    """`a && b ; c` -> [a, b, c]. Pipes and redirections other than >> .gitignore stay whole."""
    return [c.strip() for c in re.split(r"\s*(?:&&|;)\s*", command) if c.strip()]


def manual_to_ops(text: str, manual_files: list[str]) -> list[Action]:
    m = GITIGNORE.search(text)
    if m:
        parts = [x.strip(" `'\"") for x in re.split(r",|\band\b|\s+", m.group(1))]
        patterns = [x for x in parts if x and PATTERN.match(x) and x.lower() not in ("the", "a", "both", "file", "files", "folder", "folders", "them", "it", "line")]
        if patterns:
            return [Action("op", "gitignore_add", {"pattern": x}, text) for x in patterns]
    if manual_files and EDIT_VERB.search(text) and any(re.search(rf"\b{re.escape(f)}\b", text) for f in manual_files):
        return [Action("manual", text=text)]
    return [Action("manual", text=text, note="not simulated (no known file edit in it)")]


def to_ops(step: dict, repo, manual_files: list[str] | None = None) -> list[Action]:
    """step = {"command": "...", "why": "..."} from the assistant's plan."""
    command = (step.get("command") or "").strip().strip("`").strip()
    if not command:
        return manual_to_ops(step.get("why", ""), manual_files or [])
    out = []
    for cmd in split_commands(command):
        out += _one(cmd, repo, manual_files or [], step.get("why", ""))
    return out


def _one(cmd: str, repo, manual_files, why: str) -> list[Action]:
    m = ECHO_GITIGNORE.match(cmd)
    if m and ".gitignore" in cmd:
        return [Action("op", "gitignore_add", {"pattern": m.group(1)}, cmd)]
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return [Action("unsupported", text=cmd, note="cannot parse the command")]
    if not argv:
        return []
    if argv[0] != "git":
        return [Action("outside", text=cmd, note="not a git command; the user runs it outside the tool")]
    args = argv[1:]
    if args and args[0].startswith("-"):
        return [Action("unsupported", text=cmd, note="git options before the subcommand (-C, -c, --git-dir...) are not allowed")]
    if not args:
        return []
    sub, rest = args[0], args[1:]
    flags = [a for a in rest if a.startswith("-")]
    pos = [a for a in rest if not a.startswith("-")]
    A = lambda op, **p: Action("op", op, p, cmd)

    if sub in READ_ONLY or (sub == "stash" and pos[:1] in (["list"], ["show"])) or (sub == "remote" and (not pos or pos[0] in ("-v", "show"))):
        return [Action("read", text=cmd)]
    if sub in BLOCKED:
        return [A(BLOCKED[sub], args=" ".join(rest))]
    if sub == "remote":
        if pos[:1] in (["set-url"], ["add"]):
            return [A("remote_set_url" if pos[0] == "set-url" else "remote_add", args=" ".join(pos[1:]))]
        return [Action("read", text=cmd)]

    if sub in ("switch", "checkout"):
        if "--" in rest or (sub == "checkout" and pos and any((repo / p).exists() for p in pos)):
            if {"--theirs", "--ours"} & set(flags):
                side = "theirs" if "--theirs" in flags else "ours"
                paths = [p for p in pos if p != "--"]
                return [A("resolve_conflict", path=p, side=side) for p in paths]
            return [Action("unsupported", text=cmd, note="discarding file changes has no tool operation (no undo)")]
        create = {"-c", "-b", "--create", "-C", "-B"} & set(flags)
        if "--detach" in flags:
            return [Action("unsupported", text=cmd, note="detaching HEAD on purpose is not offered")]
        if create:
            if len(pos) >= 2:
                return [A("create_branch", name=pos[0], start=pos[1]), A("switch", branch=pos[0])]
            return [A("switch_create", name=pos[0])] if pos else []
        if pos == ["-"] or not pos:
            return [Action("unsupported", text=cmd, note="switch needs a branch name")]
        return [A("switch", branch=pos[0])]

    if sub == "branch":
        if not pos or {"-a", "-v", "-vv", "--list", "-r", "--all", "--show-current"} & set(flags) and not {"-d", "-D", "-f"} & set(flags):
            return [Action("read", text=cmd)]
        if "-D" in flags or ("--delete" in flags and "--force" in flags):
            return [A("delete_branch", name=pos[0])]
        if "-d" in flags or "--delete" in flags:
            return [Action("unsupported", text=cmd, note="safe delete (-d) is not offered; -D is the dangerous op")]
        if "-f" in flags or "--force" in flags or "-m" in flags or "-M" in flags:
            return [Action("unsupported", text=cmd, note="moving or renaming a branch has no tool operation")]
        return [A("create_branch", name=pos[0], **({"start": pos[1]} if len(pos) > 1 else {}))]

    if sub == "add":
        if {"-A", "--all"} & set(flags) or pos == ["."]:
            return [A("add", paths=["."])]
        if "-u" in flags or "-p" in flags:
            return [Action("unsupported", text=cmd, note="git add -u / -p is not offered; name the files")]
        return [A("add", paths=pos)] if pos else []

    if sub == "rm":
        if "--cached" in flags:
            return [A("rm_cached", paths=[p for p in pos if p != "--"])]
        return [Action("unsupported", text=cmd, note="git rm without --cached deletes files from disk")]

    if sub == "restore":
        if "--staged" in flags or "-S" in flags:
            if "--worktree" in flags or "-W" in flags:
                return [Action("unsupported", text=cmd, note="restoring the working tree discards edits")]
            return [A("restore_staged", paths=[p for p in pos if p != "--"])]
        return [Action("unsupported", text=cmd, note="discarding file changes has no tool operation (no undo)")]

    if sub == "commit":
        msg = _message(rest)
        if "--amend" in flags:
            if msg is None:
                msg = _git(repo, "log", "-1", "--format=%s") or "Amend"
            return [A("amend", message=msg)]
        ops = []
        if "-a" in flags or "--all" in flags or any(f.startswith("-") and not f.startswith("--") and "a" in f[1:] for f in flags if f != "-m"):
            changed = _git(repo, "diff", "--name-only").splitlines()
            if changed:
                ops.append(A("add", paths=changed))
        if msg is None and _in_merge(repo):
            return ops + [A("merge_commit")]
        return ops + [A("commit", message=msg or "Save changes")]

    if sub == "merge":
        if "--abort" in flags:
            return [A("merge_abort")]
        if "--continue" in flags:
            return [A("merge_commit")]
        return [A("merge", branch=pos[0])] if pos else []

    if sub == "rebase":
        if "--abort" in flags:
            return [A("rebase_abort")]
        if "--continue" in flags:
            return [A("rebase_continue")]
        if "-i" in flags or "--interactive" in flags or "--onto" in flags:
            return [Action("unsupported", text=cmd, note="interactive / --onto rebase is not offered")]
        return [A("rebase", onto=pos[0])] if pos else []

    if sub == "revert":
        if "--abort" in flags or "--continue" in flags:
            return [Action("unsupported", text=cmd, note="revert --abort/--continue is not offered")]
        return [A("revert", rev=pos[0] if pos else "HEAD")]

    if sub == "cherry-pick":
        if "--abort" in flags or "--continue" in flags:
            return [Action("unsupported", text=cmd, note="cherry-pick --abort/--continue is not offered")]
        return [A("cherry_pick", rev=pos[0])] if pos else []

    if sub == "reset":
        rev = next((p for p in pos if p != "--"), "HEAD")
        if "--hard" in flags:
            return [A("reset_hard", rev=rev)]
        if "--soft" in flags:
            return [A("reset_soft", rev=rev)]
        if "--" in rest or (pos and any((repo / p).exists() for p in pos[1:] or pos)):
            paths = [p for p in pos if p != "--" and p != "HEAD"]
            return [A("restore_staged", paths=paths)] if paths else []
        return [Action("unsupported", text=cmd, note="a mixed/keep reset has no tool operation")]

    if sub == "fetch":
        return [A("fetch", remote=pos[0] if pos else _upstream_remote(repo))]

    if sub == "pull":
        remote = pos[0] if pos else _upstream_remote(repo)
        branch = pos[1] if len(pos) > 1 else _current_branch(repo)
        if "--rebase" in flags or "-r" in flags:
            return [A("fetch", remote=remote), A("rebase", onto=f"{remote}/{branch}")]
        return [A("pull", remote=remote, branch=branch)]

    if sub == "push":
        remote = pos[0] if pos else _upstream_remote(repo)
        branch = pos[1] if len(pos) > 1 else _current_branch(repo)
        if branch == "HEAD":
            branch = _current_branch(repo)
        branch = branch.split(":")[-1]
        if {"--force", "-f", "--force-with-lease"} & set(flags) or any(f.startswith("--force-with-lease") for f in flags) \
                or any(p.startswith("+") for p in pos):
            return [A("push_force", remote=remote, branch=branch.lstrip("+"))]
        return [A("push", remote=remote, branch=branch, **({"set_upstream": True} if {"-u", "--set-upstream"} & set(flags) else {}))]

    if sub == "stash":
        if pos[:1] == ["pop"]:
            return [A("stash_pop")]
        if not pos or pos[:1] in (["push"], ["save"]):
            return [A("stash")]
        return [Action("unsupported", text=cmd, note=f"git stash {pos[0]} is not offered")]

    if sub == "clean":
        if "-n" in flags or "--dry-run" in flags:
            return [Action("read", text=cmd)]
        return [A("clean")]

    return [Action("unsupported", text=cmd, note=f"git {sub} has no tool operation")]


def _message(rest: list[str]) -> str | None:
    for i, a in enumerate(rest):
        if a in ("-m", "--message") and i + 1 < len(rest):
            return rest[i + 1]
        if a.startswith("--message="):
            return a.split("=", 1)[1]
        if a.startswith("-") and not a.startswith("--") and a.endswith("m") and i + 1 < len(rest):  # -am "msg"
            return rest[i + 1]
    return None
