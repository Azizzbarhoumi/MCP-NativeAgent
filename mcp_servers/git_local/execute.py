"""Typed git operations that change a repo, behind per-step approval.

Flow for every step:
  1. prepare(repo, op, params)  validates the arguments, rates the step, says what a destructive
     step would lose, and returns an approval token bound to (repo, op, exact params, HEAD).
  2. The user approves that one step. Dangerous steps also need the confirmation phrase.
  3. execute(token, repo, op, params, confirmation)  re-checks the binding (a changed command,
     a reused token or a moved HEAD is refused), backs up before destructive steps, runs git,
     then reports the new state in plain words and how to undo.

Tiers come from tiers.py (the one table the assistant's plan uses too): safe and careful steps
need approval; destructive = approval + confirmation phrase + automatic backup; blocked = never
runs. The approval page and the web UI ask for the code above the safe tier.
There is no raw-command tool: the model can only pick an op and fill typed parameters.
"""

import hashlib
import hmac
import json
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp_servers.git_local import inspect_repo as ir
from mcp_servers.git_local import safe_git, tiers
from mcp_servers.git_local.safe_git import GitToolError

LOG = Path(__file__).resolve().parents[2] / "logs" / "git_exec.jsonl"
TOKEN_TTL_S = 600
_SECRET = secrets.token_bytes(32)      # per process: tokens die with the server
_PENDING: dict[str, dict] = {}

CREDENTIAL = re.compile(r"(gh[pousr]_\w{16,}|github_pat_\w{16,}|glpat-\w{16,}|AKIA[0-9A-Z]{16}|"
                        r"://[^/\s:@]+:[^/\s@]+@|-----BEGIN [A-Z ]*PRIVATE KEY)")
SECRET_FILE = re.compile(r"(^|/)(\.env(\..*)?|\.git-credentials|\.netrc|id_(rsa|ed25519|ecdsa)|.*\.pem|.*\.key)$")
BLOCKED_OPS = {"config", "remote_set_url", "remote_add", "credential", "delete_repo",
               "filter_branch", "filter_repo", "gc_prune", "update_ref", "raw"}


# ------------------------------------------------------------ argument validation

def _ref(repo: Path, name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]{1,100}", name) \
            or name.endswith((".lock", "/", ".")):
        raise GitToolError(f"invalid branch name: {name!r}")
    safe_git.run(repo, ["check-ref-format", "--branch", name], read_only=True)
    return name


def _rev(repo: Path, rev: str) -> str:
    """Resolved to a full commit id, so the value passed to git can never look like an option."""
    if not isinstance(rev, str) or not re.fullmatch(r"(?!-)[A-Za-z0-9._/~^@{}-]{1,80}", rev):
        raise GitToolError(f"invalid revision: {rev!r}")
    p = safe_git.run(repo, ["rev-parse", "--verify", "--quiet", "--end-of-options", rev + "^{commit}"],
                     read_only=True, ok_codes=(0, 1))
    if not p.stdout.strip():
        raise GitToolError(f"unknown revision: {rev}")
    return p.stdout.strip()


def _remote(repo: Path, name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"(?!-)[A-Za-z0-9._-]{1,50}", name):
        raise GitToolError(f"invalid remote name: {name!r}")
    remotes = safe_git.run(repo, ["remote"], read_only=True).stdout.split()
    if name not in remotes:
        raise GitToolError(f"no remote named {name}")
    url = safe_git.run(repo, ["remote", "get-url", "--push", name], read_only=True).stdout.strip()
    # https://TOKEN@host has no ':' but is still a credential; ssh://git@host is a normal user name.
    if CREDENTIAL.search(url) or re.match(r"(?i)https?://[^/\s]+@", url):
        raise GitToolError("blocked: this remote URL contains a credential. Remove it from the URL "
                           "and use a credential manager or SSH key instead (explained, not fixed).")
    safe_git.check_local_remote(repo, url)     # a remote on this machine must not run hooks on push
    return name


def _message(msg: str) -> str:
    if not isinstance(msg, str) or not msg.strip() or len(msg) > 500 or msg.startswith("-") or "\0" in msg:
        raise GitToolError("invalid commit message (1-500 chars, must not start with '-')")
    return msg


def _paths(repo: Path, paths) -> list[str]:
    if isinstance(paths, str):
        paths = [paths]
    if not paths or len(paths) > 50:
        raise GitToolError("give 1 to 50 paths")
    return [safe_git.check_file_arg(repo, p) if p != "." else "." for p in paths]


def _no_credentials(params: dict) -> None:
    if CREDENTIAL.search(json.dumps(params)):
        raise GitToolError("blocked: an argument looks like a password or token. Never pass credentials.")


# ------------------------------------------------------------ repo facts

def _git(repo, *args, ok=(0,)):
    return safe_git.run(repo, list(args), read_only=True, ok_codes=ok).stdout


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "--verify", "--quiet", "HEAD", ok=(0, 1)).strip() or "unborn"


def _current_branch(repo: Path) -> str | None:
    out = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", ok=(0, 1)).strip()
    return out or None


def _is_pushed(repo: Path, rev: str = "HEAD") -> bool:
    return bool(_git(repo, "branch", "-r", "--contains", rev, ok=(0, 129)).strip())


def _oneline(repo: Path, *range_args: str) -> list[str]:
    return [l for l in _git(repo, "log", "--oneline", "--no-decorate", "-n50", *range_args, ok=(0, 128)).splitlines() if l]


# ------------------------------------------------------------ operations

class Op:
    """How to run one typed step. Its risk tier is NOT here: it comes from tiers.py, the one
    table the assistant's plan uses too."""
    def __init__(self, explain, build, lose=None, backup=None, undo=None):
        self.explain, self.build = explain, build
        self.lose, self.backup, self.undo = lose, backup, undo


OPS: dict[str, Op] = {
    "switch": Op("Move to another branch.",
                 lambda r, p: ["switch", "--end-of-options", _ref(r, p["branch"])],
                 lose=lambda r, p: _oneline(r, "HEAD", "--not", "--branches", "--tags", "--remotes"),
                 backup="head", undo=lambda r, p, before: f"git switch {before['branch'] or '--detach ' + before['head'][:12]}"),
    "create_branch": Op("Create a branch at a commit (default: where you are). Nothing moves.",
                        lambda r, p: ["branch", "--end-of-options", _ref(r, p["name"]),
                                      *([_rev(r, p["start"])] if p.get("start") else [])],
                        undo=lambda r, p, b: f"git branch -d {p['name']}"),
    "switch_create": Op("Create a branch where you are and move onto it. Saves detached commits.",
                        lambda r, p: ["switch", "-c", _ref(r, p["name"])],
                        undo=lambda r, p, b: f"git switch {b['branch'] or '--detach ' + b['head'][:12]}  then  git branch -d {p['name']}"),
    "add": Op("Stage files for the next commit.",
              lambda r, p: ["add", "--", *_paths(r, p["paths"])],
              undo=lambda r, p, b: "git restore --staged -- " + " ".join(p["paths"] if isinstance(p["paths"], list) else [p["paths"]])),
    "rm_cached": Op("Stop tracking files. They stay on your disk.",
                    lambda r, p: ["rm", "--cached", "-r", "--", *_paths(r, p["paths"])],
                    undo=lambda r, p, b: "git add -- " + " ".join(p["paths"] if isinstance(p["paths"], list) else [p["paths"]])),
    "restore_staged": Op("Unstage files. Your edits stay.",
                         lambda r, p: ["restore", "--staged", "--", *_paths(r, p["paths"])],
                         undo=lambda r, p, b: "git add -- (same files)"),
    "gitignore_add": Op("Add a pattern to .gitignore so git stops offering that file.",
                        None, undo=lambda r, p, b: f"remove the line {p['pattern']!r} from .gitignore"),
    "resolve_conflict": Op("Settle a conflicted file by keeping one side ('ours' = your branch, "
                           "'theirs' = the branch being merged). Stage it with add afterwards.",
                           lambda r, p: ["checkout", "--" + {"ours": "ours", "theirs": "theirs"}[p["side"]], "--",
                                         *_paths(r, p["path"])],
                           undo=lambda r, p, b: f"git checkout -m -- {p['path']}  (brings the conflict markers back)"),
    "commit": Op("Record the staged changes as a commit.",
                 lambda r, p: ["commit", "--no-verify", "-m", _message(p["message"])],
                 undo=lambda r, p, b: f"git reset --soft {b['head'][:12]}  (keeps the changes staged)"),
    "merge_commit": Op("Finish a merge after all conflicts are resolved.",
                       lambda r, p: ["commit", "--no-verify", "--no-edit"],
                       undo=lambda r, p, b: f"git reset --merge {b['head'][:12]}"),
    "merge": Op("Bring another branch's commits into this one.",
                lambda r, p: ["merge", "--no-edit", "--no-verify", "--end-of-options", _ref(r, p["branch"])],
                undo=lambda r, p, b: f"git merge --abort (if it stopped on conflicts) or git reset --merge {b['head'][:12]}"),
    "merge_abort": Op("Cancel the merge in progress and go back to before it.",
                      lambda r, p: ["merge", "--abort"], undo=lambda r, p, b: "run the merge again"),
    "rebase_abort": Op("Cancel the rebase in progress and put the branch back as it was.",
                       lambda r, p: ["rebase", "--abort"], undo=lambda r, p, b: "run the rebase again"),
    "rebase_continue": Op("Go on with the rebase after the conflicts are resolved and staged.",
                          lambda r, p: ["rebase", "--continue"],
                          undo=lambda r, p, b: f"git reset --keep ORIG_HEAD  (the branch before the rebase)"),
    "revert": Op("Add a new commit that undoes an old one. History is kept, safe on shared branches.",
                 lambda r, p: ["revert", "--no-edit", _rev(r, p["rev"])],
                 undo=lambda r, p, b: f"git reset --keep {b['head'][:12]}  (only if not pushed yet)"),
    "cherry_pick": Op("Copy one commit onto the current branch.",
                      lambda r, p: ["cherry-pick", _rev(r, p["rev"])],
                      undo=lambda r, p, b: f"git reset --keep {b['head'][:12]}"),
    "reset_soft": Op("Move the branch back; the undone changes stay staged.",
                     lambda r, p: ["reset", "--soft", _rev(r, p["rev"])],
                     undo=lambda r, p, b: f"git reset --soft {b['head'][:12]}"),
    "amend": Op("Replace the last commit with the staged changes (and a new message).",
                lambda r, p: ["commit", "--amend", "--no-verify", "-m", _message(p["message"])],
                lose=lambda r, p: ["the current last commit is already on the remote; replacing it needs a force-push"]
                if _is_pushed(r) else [], backup="head",
                undo=lambda r, p, b: f"git reset --soft {b['head'][:12]}"),
    "fetch": Op("Download new commits from the remote. Your branches don't move.",
                lambda r, p: ["fetch", "--end-of-options", _remote(r, p["remote"])], undo=lambda r, p, b: "nothing to undo"),
    "pull": Op("Download the remote branch and merge it into yours.",
               lambda r, p: ["pull", "--no-rebase", "--no-edit", "--end-of-options", _remote(r, p["remote"]), _ref(r, p["branch"])],
               undo=lambda r, p, b: f"git reset --merge {b['head'][:12]}"),
    "push": Op("Publish your commits to the remote. Refused by git if the remote has commits you lack.",
               lambda r, p: ["push", *(["--set-upstream"] if p.get("set_upstream") else []), "--no-verify",
                             "--end-of-options", _remote(r, p["remote"]), _ref(r, p["branch"])],
               undo=lambda r, p, b: "pushed commits are shared now; undo with git revert, not by rewriting"),
    "stash": Op("Put uncommitted changes aside.", lambda r, p: ["stash", "push"],
                undo=lambda r, p, b: "git stash pop"),
    "stash_pop": Op("Bring back the last stashed changes.", lambda r, p: ["stash", "pop"],
                    undo=lambda r, p, b: "git stash  (puts them aside again)"),
    # ---- destructive (tier from tiers.py; these declare what they would lose and how to back up)
    "reset_hard": Op("Move the branch to a commit and THROW AWAY all uncommitted changes.",
                     lambda r, p: ["reset", "--hard", _rev(r, p["rev"])],
                     lose=lambda r, p: _oneline(r, f"{_rev(r, p['rev'])}..HEAD") +
                     [f"uncommitted: {l}" for l in _git(r, "diff", "HEAD", "--stat", "--no-ext-diff").splitlines()[:-1]],
                     backup="head+worktree"),
    "push_force": Op("Overwrite the remote branch with yours (--force-with-lease).",
                     lambda r, p: ["push", "--force-with-lease", "--no-verify", "--end-of-options",
                                   _remote(r, p["remote"]), _ref(r, p["branch"])],
                     lose=lambda r, p: [f"on the remote: {l}" for l in
                                        _oneline(r, f"HEAD..refs/remotes/{_remote(r, p['remote'])}/{_ref(r, p['branch'])}")],
                     backup="remote"),
    "clean": Op("Delete untracked files and folders from disk (git has no copy of them).",
                lambda r, p: ["clean", "-f", "-d"],
                lose=lambda r, p: _git(r, "clean", "-n", "-d").splitlines(), backup="untracked"),
    "rebase": Op("Replay your commits on top of another branch. Rewrites their ids.",
                 lambda r, p: ["rebase", "--no-verify", "--end-of-options", _ref(r, p["onto"])],
                 lose=lambda r, p: [f"rewritten: {l}" for l in _oneline(r, f"{_ref(r, p['onto'])}..HEAD")],
                 backup="head"),
    "delete_branch": Op("Delete a branch even if its commits are not merged anywhere.",
                        lambda r, p: ["branch", "-D", "--end-of-options", _ref(r, p["name"])],
                        lose=lambda r, p: _oneline(r, _ref(r, p["name"]), "--not",
                                                   *[b for b in _git(r, "for-each-ref", "--format=%(refname)", "refs/heads").split()
                                                     if b != f"refs/heads/{p['name']}"]),
                        backup="branch"),
}


# ------------------------------------------------------------ prepare / execute

def _binding(repo: Path, op: str, params: dict) -> str:
    return json.dumps({"repo": str(repo), "op": op, "params": params, "head": _head(repo)}, sort_keys=True)


def _sign(binding: str, nonce: str) -> str:
    return hmac.new(_SECRET, (binding + nonce).encode(), hashlib.sha256).hexdigest()


def _log(task_id: str, **fields) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "task_id": task_id, **fields}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(safe_git.redact(json.dumps(row, default=str)) + "\n")


def _tier(op: str, repo, params) -> str:
    return tiers.decide(op, tiers.repo_facts(repo, op, params))[0]


def _secret_files_staged_by(repo: Path, params: dict) -> list[str]:
    paths = params.get("paths")
    paths = [paths] if isinstance(paths, str) else (paths or [])
    out = _git(repo, "ls-files", "--others", "--modified", "--exclude-standard", "--", *paths)
    return [f for f in out.splitlines() if SECRET_FILE.search(f)]


def _files_with_conflict_markers(repo: Path, params: dict) -> list[str]:
    conflicted = _git(repo, "diff", "--name-only", "--diff-filter=U").splitlines()
    paths = params.get("paths")
    paths = [paths] if isinstance(paths, str) else (paths or [])
    out = []
    for f in conflicted:
        if "." in paths or f in paths or any(f.startswith(p.rstrip("/") + "/") for p in paths):
            text = (repo / f).read_text(encoding="utf-8", errors="replace") if (repo / f).is_file() else ""
            if re.search(r"^<<<<<<< ", text, re.M) and re.search(r"^>>>>>>> ", text, re.M):
                out.append(f)
    return out


def prepare(path: str, op: str, params: dict | None, task_id: str = "-") -> dict:
    params = dict(params or {})
    try:
        repo = safe_git.check_repo(path)
        if op in BLOCKED_OPS:
            raise GitToolError(f"blocked: '{op}' is never run by the assistant")
        if op not in OPS:
            raise GitToolError(f"blocked: unknown operation '{op}'")
        _no_credentials(params)
        spec = OPS[op]
        if op == "add":
            marked = _files_with_conflict_markers(repo, params)
            if marked:
                raise GitToolError(f"blocked: {', '.join(marked)} still has conflict markers "
                                   "(<<<<<<< ======= >>>>>>>). Edit the file or use resolve_conflict first.")
            leaked = _secret_files_staged_by(repo, params)
            if leaked:
                raise GitToolError(f"blocked: this would stage files that usually hold secrets: {', '.join(leaked)}")
        if op == "gitignore_add":
            pattern = params.get("pattern", "")
            if not re.fullmatch(r"[A-Za-z0-9._*/!-]{1,100}", pattern) or pattern.startswith("-"):
                raise GitToolError(f"invalid .gitignore pattern: {pattern!r}")
            preview = f"append {pattern!r} to .gitignore"
        else:
            preview = "git " + " ".join(spec.build(repo, params))   # validates every argument
        tier = _tier(op, repo, params)
        lose = (spec.lose(repo, params) if spec.lose else []) if tier == "destructive" else []
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        # Missing or wrong-type parameter from the model (e.g. side="sideways"): refuse, never crash.
        err = f"blocked: invalid parameters for '{op}' ({type(e).__name__}: {str(e)[:80]})"
        _log(task_id, op=op, params=_redacted(params), tier="blocked", outcome="refused", error=safe_git.redact(err))
        return {"op": op, "tier": "blocked", "explain": safe_git.redact(err)}
    except GitToolError as e:
        _log(task_id, op=op, params=_redacted(params), tier="blocked", outcome="refused", error=safe_git.redact(str(e)))
        return {"op": op, "tier": "blocked", "explain": safe_git.redact(str(e))}

    binding = _binding(repo, op, params)
    nonce = secrets.token_hex(8)
    token = f"{nonce}.{_sign(binding, nonce)}"
    entry = {"binding": binding, "expires": time.time() + TOKEN_TTL_S, "tier": tier}
    out = {"op": op, "command": preview, "tier": tier, "explain": spec.explain, "approval_token": token}
    if tier == "destructive":
        phrase = f"I accept the risk: {op}"
        entry["phrase"] = phrase
        out.update(would_lose=lose[:30] or ["nothing found to lose right now"], confirm_phrase=phrase,
                   backup="a backup is created automatically before this step runs")
    _PENDING[token] = entry
    _log(task_id, op=op, params=_redacted(params), tier=tier, outcome="prepared")
    return out


def _redacted(params: dict) -> dict:
    return json.loads(safe_git.redact(CREDENTIAL.sub("***", json.dumps(params))))


def _make_backup(repo: Path, kind: str, op: str, params: dict) -> list[str]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    base = f"refs/assistant-backup/{stamp}-{op}"
    w = lambda *a: safe_git.run(repo, list(a), read_only=False)
    made = []
    if "head" in kind and _head(repo) != "unborn":
        w("update-ref", f"{base}/head", _head(repo)); made.append(f"{base}/head")
    if "worktree" in kind:
        conflicted = _git(repo, "diff", "--name-only", "--diff-filter=U").splitlines()
        if conflicted:
            # `git stash create` refuses an index with conflicts: copy the files instead.
            dest = repo / ".git" / "assistant-backup" / stamp
            for rel in conflicted + _git(repo, "diff", "--name-only", "HEAD").splitlines():
                src = repo / rel
                if src.is_file() and not src.is_symlink():
                    (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest / rel)
            made.append(f"copied the changed and conflicted files to {dest}")
        else:
            stash = _git(repo, "stash", "create").strip()
            if stash:
                w("update-ref", f"{base}/uncommitted", stash); made.append(f"{base}/uncommitted (git stash apply {base}/uncommitted)")
    if kind == "branch":
        tip = _rev(repo, params["name"])
        w("update-ref", f"{base}/branch", tip); made.append(f"{base}/branch (git branch {params['name']} {tip[:12]})")
    if kind == "remote":
        ref = f"refs/remotes/{params['remote']}/{params['branch']}"
        tip = _git(repo, "rev-parse", "--verify", "--quiet", ref, ok=(0, 1)).strip()
        if tip:
            w("update-ref", f"{base}/remote", tip); made.append(f"{base}/remote (what the remote had: {tip[:12]})")
    if kind == "untracked":
        dest = repo / ".git" / "assistant-backup" / stamp
        for line in _git(repo, "clean", "-n", "-d").splitlines():
            rel = line.removeprefix("Would remove ").strip()
            src = repo / rel
            if src.is_symlink():
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, target)
        made.append(f"copied the untracked files to {dest}")
    return made


def _plain_state(s: dict) -> str:
    if not s.get("is_repo"):
        return "not a repo"
    where = f"on branch {s['branch']}" if s["branch"] else f"detached at {s['head']}"
    parts = [where]
    if s.get("upstream"):
        parts.append(f"{s['ahead']} ahead / {s['behind']} behind {s['upstream']}")
    if s.get("in_progress"):
        parts.append(f"a {s['in_progress']} is in progress")
    for k in ("conflicted", "staged", "unstaged", "untracked"):
        if s[k]["count"]:
            parts.append(f"{s[k]['count']} {k}")
    return ", ".join(parts) + ("" if any(s[k]["count"] for k in ("conflicted", "staged", "unstaged", "untracked"))
                               else ", working tree clean")


def execute(token: str, path: str, op: str, params: dict | None, confirmation: str | None = None,
            task_id: str = "-") -> dict:
    params = dict(params or {})
    entry = _PENDING.pop(token, None)      # single use: popped even if the checks below fail
    try:
        repo = safe_git.check_repo(path)
        if entry is None:
            raise GitToolError("refused: unknown or already used approval token")
        if time.time() > entry["expires"]:
            raise GitToolError("refused: approval expired; prepare the step again")
        nonce, sig = token.split(".", 1)
        binding = _binding(repo, op, params)
        if binding != entry["binding"] or not hmac.compare_digest(sig, _sign(binding, nonce)):
            raise GitToolError("refused: this approval was for a different command or repo state; "
                               "prepare and approve the step again")
        if entry["tier"] == "destructive" and confirmation != entry["phrase"]:
            raise GitToolError(f"refused: destructive step needs the confirmation phrase: {entry['phrase']}")
        spec = OPS[op]
        before = {"head": _head(repo), "branch": _current_branch(repo)}
        # A backup before every step that rewrites or deletes: all destructive ones, and careful
        # ones that declare one (a rebase of local-only commits).
        backups = _make_backup(repo, spec.backup, op, params) if entry["tier"] != "safe" and spec.backup else []
        if op == "gitignore_add":
            gi = repo / ".gitignore"
            safe_git.check_file_arg(repo, ".gitignore")
            text = gi.read_text(encoding="utf-8") if gi.exists() else ""
            if params["pattern"] not in text.splitlines():
                gi.write_text(text + ("" if text.endswith("\n") or not text else "\n") + params["pattern"] + "\n",
                              encoding="utf-8", newline="\n")
            out_text = ""
        else:
            p = safe_git.run(repo, spec.build(repo, params), read_only=False, ok_codes=(0, 1))
            out_text = safe_git.redact((p.stdout + p.stderr).strip())
            if p.returncode:
                state = ir.repo_state(str(repo))
                _log(task_id, op=op, params=_redacted(params), tier=entry["tier"], outcome="git_failed", error=out_text[:300])
                return {"ok": False, "op": op, "output": out_text[-1500:], "state": _plain_state(state),
                        "undo": spec.undo(repo, params, before) if spec.undo else None, "backups": backups}
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        _log(task_id, op=op, params=_redacted(params), outcome="refused", error=f"invalid parameters: {type(e).__name__}")
        return {"ok": False, "op": op, "refused": f"refused: invalid parameters for '{op}'"}
    except GitToolError as e:
        _log(task_id, op=op, params=_redacted(params), outcome="refused", error=safe_git.redact(str(e)))
        return {"ok": False, "op": op, "refused": safe_git.redact(str(e))}

    state = ir.repo_state(str(repo))
    undo = [f"git reset --hard {b}" for b in backups if b.endswith("/head")]
    undo += [b.split("(", 1)[1].rstrip(")") for b in backups if "(" in b]
    if spec.undo:
        undo.append(spec.undo(repo, params, before))
    _log(task_id, op=op, params=_redacted(params), tier=entry["tier"], outcome="done",
         head_before=before["head"], head_after=_head(repo), backups=backups)
    return {"ok": True, "op": op, "output": out_text[-1500:], "state": _plain_state(state),
            "head_before": before["head"][:12], "head_after": _head(repo)[:12],
            "undo": undo, "backups": backups,
            "log": ir.log(str(repo), 5, all_branches=False)}


# ------------------------------------------------------------ approval for the MCP server
#
# prepare() returns the token to its caller. That is right for agent code running in-process
# (the benchmark, a UI), and WRONG for the MCP server: there the caller is the model, which could
# then approve its own step. So the server uses the two functions below instead:
#   prepare_for_model  keeps the token here and gives the model only a step id. A random
#                      approval code is shown to the HUMAN outside the model's context (stderr of
#                      the server process and logs/approvals.log), like a 2FA code.
#   execute_with_code  runs the step only with that code (and the phrase for destructive steps).
#                      Three wrong codes cancel the step.

APPROVALS_LOG = LOG.with_name("approvals.log")
APPROVAL_PAGE = False      # set by approval_ui.start(); off by default until the page is reviewed
_STEPS: dict[str, dict] = {}
_DECIDED: dict[str, str] = {}          # step id -> "rejected" / "cancelled" / "used", for step_status
MAX_CODE_ATTEMPTS = 3
_LOCK = threading.Lock()               # the approval page runs in another thread


def _show_code_to_human(step_id: str, command: str, tier: str, code: str) -> None:
    import sys
    line = f"[git-local] approve step {step_id} ({tier}): {command}  ->  approval code: {code}"
    print(line, file=sys.stderr, flush=True)
    APPROVALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with APPROVALS_LOG.open("a", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat(timespec="seconds") + " " + safe_git.redact(line) + "\n")


def prepare_for_model(path: str, op: str, params: dict | None, task_id: str = "-",
                      announce_safe: bool = True) -> dict:
    """announce_safe=False (the web UI): no code is printed for a safe step, because there it
    runs with one click (run_safe_step). The MCP server keeps the default: the model needs the
    code for every step."""
    p = prepare(path, op, params, task_id)
    if p["tier"] == "blocked":
        return p
    step_id = secrets.token_hex(4)
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
    with _LOCK:
        _STEPS[step_id] = {"token": p.pop("approval_token"), "code_hash": hashlib.sha256(code.encode()).hexdigest(),
                           "path": path, "op": op, "params": dict(params or {}), "attempts": 0,
                           "phrase": p.get("confirm_phrase"),
                           # shown on the human's approval page only, never returned to the model
                           "human": {"code": code, "command": p["command"], "tier": p["tier"], "explain": p["explain"],
                                     "would_lose": p.get("would_lose", []), "repo": path, "phrase": p.get("confirm_phrase"),
                                     "created": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                           "approved": False, "confirmation": None}
    if announce_safe or p["tier"] != "safe":
        _show_code_to_human(step_id, p["command"], p["tier"], code)
    if APPROVAL_PAGE:
        how = ("They approve it on their git-local approval page (then call execute_step with the step_id "
               "only), or they type you the approval code from that page.")
    else:
        how = ("They type you the approval code that git-local showed them (in its console and "
               "logs/approvals.log); then call execute_step with the step_id and that code.")
    p.update(step_id=step_id, approval="Show this step to the user and wait. " + how)
    return p


# ---- used by the human's approval page (approval_ui.py), never exposed as an MCP tool

def pending_for_human() -> list[dict]:
    with _LOCK:
        return [{"step_id": sid, "approved": st["approved"], **st["human"]}
                for sid, st in _STEPS.items()]


def approve_by_human(step_id: str, confirmation: str | None = None) -> dict:
    with _LOCK:
        st = _STEPS.get(step_id)
        if st is None:
            return {"ok": False, "error": "unknown or finished step"}
        if st["phrase"] and confirmation != st["phrase"]:
            return {"ok": False, "error": f"destructive step: type exactly '{st['phrase']}' to approve"}
        st.update(approved=True, confirmation=confirmation if st["phrase"] else None)
    return {"ok": True}


def reject_by_human(step_id: str, task_id: str = "-") -> dict:
    with _LOCK:
        st = _STEPS.pop(step_id, None)
        if st is None:
            return {"ok": False, "error": "unknown or finished step"}
        _PENDING.pop(st["token"], None)
        _DECIDED[step_id] = "rejected"
    _log(task_id, op=st["op"], outcome="refused", error="rejected by the user on the approval page")
    return {"ok": True}


def run_safe_step(step_id: str, task_id: str = "-") -> dict:
    """For a human UI: run a prepared step WITHOUT a code, but only if its tier is safe.
    Everything above safe goes through execute_with_code (the code, and the phrase when
    destructive). The rule lives here, not in the UI, so no page can skip it."""
    with _LOCK:
        st = _STEPS.get(step_id)
        if st is None:
            return {"ok": False, "refused": "refused: unknown, used or cancelled step id; prepare it again"}
        if st["human"]["tier"] != "safe":
            return {"ok": False, "refused": f"refused: a {st['human']['tier']} step needs the approval code"}
        _STEPS.pop(step_id, None)
        _DECIDED[step_id] = "used"
    return execute(st["token"], st["path"], st["op"], st["params"], None, task_id)


def human_view(step_id: str) -> dict | None:
    """What the human's own page shows for one step (includes the code). Never for the model."""
    with _LOCK:
        st = _STEPS.get(step_id)
        return None if st is None else {"step_id": step_id, "approved": st["approved"], **st["human"]}


def step_status(step_id: str) -> dict:
    """For the model: pending, approved, rejected, cancelled or used. Never the code."""
    with _LOCK:
        st = _STEPS.get(step_id)
        if st is not None:
            return {"step_id": step_id, "status": "approved" if st["approved"] else "pending"}
        return {"step_id": step_id, "status": _DECIDED.get(step_id, "unknown")}


def execute_with_code(step_id: str, approval_code: str | None = None, confirmation: str | None = None,
                      task_id: str = "-") -> dict:
    with _LOCK:
        step = _STEPS.get(step_id)
        if step is None:
            return {"ok": False, "refused": "refused: unknown, used or cancelled step id; prepare it again"}
        if step["approved"]:                      # the user clicked Approve on the approval page
            _STEPS.pop(step_id, None)
            _DECIDED[step_id] = "used"
            confirmation = step["confirmation"] if step["phrase"] else confirmation
        elif not approval_code:
            return {"ok": False, "refused": "waiting: the user has not approved this step yet"}
        elif not hmac.compare_digest(hashlib.sha256(str(approval_code).encode()).hexdigest(), step["code_hash"]):
            step["attempts"] += 1
            if step["attempts"] >= MAX_CODE_ATTEMPTS:
                _STEPS.pop(step_id, None)
                _PENDING.pop(step["token"], None)
                _DECIDED[step_id] = "cancelled"
                _log(task_id, op=step["op"], outcome="refused", error="approval code wrong 3 times; step cancelled")
                return {"ok": False, "refused": "refused: wrong approval code 3 times; the step is cancelled"}
            _log(task_id, op=step["op"], outcome="refused", error="wrong approval code")
            return {"ok": False, "refused": "refused: wrong approval code (only the user has it)"}
        else:
            _STEPS.pop(step_id, None)
            _DECIDED[step_id] = "used"
    return execute(step["token"], step["path"], step["op"], step["params"], confirmation, task_id)
