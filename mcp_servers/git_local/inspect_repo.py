"""Read-only inspection of a local git repo. No MCP here: plain functions, easy to test.

Safety: every call goes through safe_git (hostile-config check, hooks/fsmonitor/filters
off, clean environment, no shell, timeout, allowed folders, no symlinks). On top of that:
- Only subcommands in READ_ONLY can run; arguments are built by this module, never by the
  model.
- GIT_OPTIONAL_LOCKS=0: `git status` would otherwise refresh the index file. Now it
  writes nothing at all.
- Remote URLs are redacted: https://user:token@host -> https://***@host.
- We always report the repo root git resolved, and warn when it is ABOVE the folder asked
  about (example: a home folder that was turned into a repo by mistake).
"""

import re
from pathlib import Path

from mcp_servers.git_local import safe_git
from mcp_servers.git_local.safe_git import GitToolError, allowed_roots, check_folder  # noqa: F401 (re-export)

MAX_ITEMS = 50          # cap on file lists, so a huge repo can't flood the model's context
MAX_TEXT = 6000         # cap on diff / log text
READ_ONLY = {"rev-parse", "status", "log", "diff", "remote", "stash", "reflog", "branch",
             "show", "ls-files", "for-each-ref", "merge-base", "rev-list", "cat-file"}


def redact_url(url: str) -> str:
    """Hide credentials embedded in a remote URL (tokens are often pasted there)."""
    return re.sub(r"(://)[^/@\s]+@", r"\1***@", url)


def check_path(path: str) -> Path:
    """Repo root inside the allowed folders, config checked (see safe_git.check_repo)."""
    return safe_git.check_repo(path)


def _git(repo: Path, *args: str, ok_codes=(0,)) -> str:
    if args[0] not in READ_ONLY:
        raise GitToolError(f"git {args[0]} is not a read-only command")
    return safe_git.run(repo, list(args), read_only=True, ok_codes=ok_codes).stdout


def find_repo_above(folder: Path) -> Path | None:
    """Look for a .git folder in the parents WITHOUT running git there (it may be outside the
    allowed folders, like a home folder that was turned into a repo by mistake)."""
    for parent in folder.parents:
        if (parent / ".git").exists():
            return parent
    return None


def _cap(items: list[str]) -> dict:
    return {"count": len(items), "items": items[:MAX_ITEMS], "truncated": len(items) > MAX_ITEMS}


def _in_progress(git_dir: Path) -> str | None:
    for marker, name in [("MERGE_HEAD", "merge"), ("rebase-merge", "rebase"), ("rebase-apply", "rebase"),
                         ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
                         ("BISECT_LOG", "bisect")]:
        if (git_dir / marker).exists():
            return name
    return None


def repo_state(path: str) -> dict:
    """Everything a beginner's question usually depends on, in one call."""
    asked = check_folder(path)
    if not (asked / ".git").exists():
        above = find_repo_above(asked)
        out = {"asked": str(asked), "is_repo": False,
               "explain": "This folder is not the root of a git repository."}
        if above:
            out["warnings"] = [f"A parent folder, {above}, is a git repo, so git commands here act on "
                               "that whole repo." + (" It is your home folder: usually a mistake."
                                                     if above == Path.home().resolve() else "")]
        return out
    root = check_path(str(asked))
    git_dir = root / ".git"
    warnings = []
    if root == Path.home().resolve():
        warnings.append("Your home folder is a git repo. This is usually a mistake.")

    # Porcelain v2: stable, machine-readable status with branch info.
    st = _git(root, "status", "--porcelain=v2", "--branch", "--untracked-files=normal")
    branch, upstream, ahead, behind, head = None, None, 0, 0, None
    staged, unstaged, untracked, conflicted = [], [], [], []
    for line in st.splitlines():
        if line.startswith("# branch.oid "):
            head = line.split()[2][:7]
        elif line.startswith("# branch.head "):
            b = line.split(" ", 2)[2]
            branch = None if b == "(detached)" else b
        elif line.startswith("# branch.upstream "):
            upstream = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            a, b = line.split()[2:4]
            ahead, behind = int(a), -int(b)
        elif line.startswith(("1 ", "2 ")):
            fields = 8 if line[0] == "1" else 9   # type 2 (rename) has one more field
            xy, file = line.split(" ")[1], line.split(" ", fields)[-1].split("\t")[0]
            if xy[0] != ".":
                staged.append(file)
            if xy[1] != ".":
                unstaged.append(file)
        elif line.startswith("u "):
            conflicted.append(line.split(" ", 10)[-1])
        elif line.startswith("? "):
            untracked.append(line[2:])

    remotes = {}
    for line in _git(root, "remote", "-v").splitlines():
        name, rest = line.split("\t", 1)
        url, kind = rest.rsplit(" ", 1)     # URLs / Windows paths can contain spaces
        if kind == "(push)":
            remotes[name] = redact_url(url)
    stashes = len(_git(root, "stash", "list").splitlines())

    return {
        "asked": str(asked), "is_repo": True, "root": str(root), "warnings": warnings,
        "branch": branch, "detached": branch is None, "head": head,
        "upstream": upstream, "ahead": ahead, "behind": behind,
        "in_progress": _in_progress(git_dir),
        "conflicted": _cap(conflicted), "staged": _cap(staged),
        "unstaged": _cap(unstaged), "untracked": _cap(untracked),
        "remotes": remotes, "stashes": stashes,
    }


def log(path: str, n: int = 15, all_branches: bool = True) -> str:
    root = check_path(path)
    n = max(1, min(int(n), 50))
    args = ["log", "--oneline", "--decorate", "--graph", f"-n{n}"] + (["--all"] if all_branches else [])
    return _git(root, *args, ok_codes=(0, 128))[:MAX_TEXT]


def diff(path: str, staged: bool = False, file: str | None = None) -> str:
    root = check_path(path)
    args = ["diff", "--no-ext-diff", "--no-textconv", "--stat", "--patch"] + (["--cached"] if staged else [])
    if file:
        args += ["--", safe_git.check_file_arg(root, file)]
    return _git(root, *args)[:MAX_TEXT]


def reflog(path: str, n: int = 15) -> str:
    """Where HEAD has been: the safety net for 'I lost my commit'."""
    root = check_path(path)
    return _git(root, "reflog", f"-n{max(1, min(int(n), 50))}", ok_codes=(0, 128))[:MAX_TEXT]


def show_commit(path: str, rev: str = "HEAD") -> str:
    root = check_path(path)
    if not re.fullmatch(r"[\w./~^@{}-]{1,80}", rev) or rev.startswith("-"):
        raise GitToolError("invalid revision")
    return _git(root, "show", "--no-ext-diff", "--no-textconv", "--stat", rev)[:MAX_TEXT]
