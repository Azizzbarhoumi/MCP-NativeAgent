"""The only place that starts git. Every read and every write goes through `run`.

A repo is untrusted input: its .git/config can make git run programs (hooks, fsmonitor,
filters, textconv, custom merge drivers, credential helpers, upload/receive-pack, ssh
commands, aliases) and can pull in other config files (include, includeIf) or point the
work tree outside the folder (core.worktree). So:

1. Before touching a repo we read its config FILE with `git config --file ... --no-includes`
   (no repo is opened, nothing can run) and refuse the repo if it sets any dangerous key.
2. Every call also overrides the risky settings on the command line (-c wins over repo
   config), points core.hooksPath at an empty folder, and ignores system and global config.
3. The environment is rebuilt from a short allowlist, so GIT_DIR, GIT_CONFIG_PARAMETERS,
   GIT_SSH_COMMAND or LD_PRELOAD from the caller can't leak in.
4. No shell, a timeout, and capped output.
5. Paths: every path must be inside an allowed root, with no symlink on the way.
"""

import os
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

TIMEOUT_S = 30
MAX_OUTPUT = 20_000

DEFAULT_ROOTS = [Path(__file__).resolve().parents[2] / "eval" / "git_scenarios" / "build"]

# Keys in a repo's own config that can run programs, read other files, or redirect git.
HOSTILE_KEY = re.compile(
    r"^(core\.(fsmonitor|hookspath|sshcommand|pager|editor|askpass|worktree|gitproxy)"
    r"|include\.|includeif\.|filter\.|alias\.|credential|gpg\.|sequence\.editor"
    r"|diff\.[^.]+\.(textconv|command)|merge\.[^.]+\.driver"
    r"|remote\.[^.]+\.(uploadpack|receivepack|proxy|vcs)|uploadpack\.|receive\."
    r"|http\.|url\.|core\.alternaterefscommand|mergetool\.|difftool\.|trailer\.|submodule\.)", re.I)

# Secrets in text the tool returns or logs (diffs, commit messages, git errors): replaced by ***.
SECRET_PATTERNS = [
    re.compile(r"(gh[pousr]_)\w{16,}|(github_pat_)\w{16,}|(glpat-)[\w-]{16,}|(AKIA)[0-9A-Z]{16}"),
    re.compile(r"(://)[^/\s:@]+:[^/\s@]+@"),                                   # https://user:pass@host
    re.compile(r"(?m)^([+\- ]?\s*(?:export\s+)?[A-Za-z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Za-z0-9_]*\s*[=:]\s*)"
               r"(?![\s\"']*$)(?!.*os\.environ)[^\n]+", re.I),                           # KEY=value lines (diffs, .env)
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"),
]


def redact(text: str) -> str:
    """Hide tokens, passwords in URLs, KEY=value secrets and private keys in any text."""
    if not text:
        return text
    text = SECRET_PATTERNS[0].sub(lambda m: next(g for g in m.groups() if g) + "***", text)
    text = SECRET_PATTERNS[1].sub(r"\1***@", text)
    text = SECRET_PATTERNS[2].sub(lambda m: m.group(1) + "***", text)
    text = SECRET_PATTERNS[3].sub("-----BEGIN PRIVATE KEY----- *** (redacted)", text)
    return text

ENV_KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR")


class GitToolError(Exception):
    """Refused or failed. The message is safe to show the user."""


@lru_cache
def _sandbox() -> tuple[str, str]:
    """An empty hooks folder and an empty global config file, made once per process."""
    d = Path(tempfile.mkdtemp(prefix="git-assistant-"))
    (d / "hooks").mkdir()
    (d / "empty.gitconfig").write_text("", encoding="utf-8")
    return str(d / "hooks"), str(d / "empty.gitconfig")


def overrides() -> list[str]:
    hooks, _ = _sandbox()
    pairs = {
        "core.hooksPath": hooks, "core.fsmonitor": "false", "core.pager": "cat",
        "core.editor": "true", "sequence.editor": "true", "core.askPass": "",
        "credential.helper": "", "diff.external": "", "commit.gpgSign": "false",
        "tag.gpgSign": "false", "protocol.ext.allow": "never", "core.symlinks": "false",
        "submodule.recurse": "false", "fetch.recurseSubmodules": "false",   # no surprise fetches of other URLs
        "protocol.file.allow": "user",
    }
    out = []
    for k, v in pairs.items():
        out += ["-c", f"{k}={v}"]
    return out


@lru_cache
def identity() -> dict:
    """Commits need a name and email. Global config is ignored at run time, so read just
    these two values once (`git config --get` executes nothing)."""
    def get(key):
        p = subprocess.run(["git", "config", "--global", "--get", key], capture_output=True, text=True)
        return p.stdout.strip()
    name, email = get("user.name") or "Git Assistant", get("user.email") or "assistant@localhost.invalid"
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


def clean_env(read_only: bool) -> dict:
    _, empty = _sandbox()
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": empty, "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat", "GIT_EDITOR": "true", "LC_ALL": "C",
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",  # env beats core.sshCommand; never prompts
        "HOME": tempfile.gettempdir(),               # no ~/.gitconfig, no ~/.ssh/config surprises
    })
    if read_only:
        env["GIT_OPTIONAL_LOCKS"] = "0"  # `git status` must not rewrite the index
    else:
        env.update(identity())
    return env


# ------------------------------------------------------------------ paths

def allowed_roots() -> list[Path]:
    env = os.environ.get("GIT_TOOL_ALLOWED_ROOTS")
    roots = [Path(p) for p in env.split(os.pathsep) if p] if env else DEFAULT_ROOTS
    return [r.expanduser().resolve() for r in roots]


def _no_symlinks(base: Path, target: Path) -> None:
    cur = base
    for part in target.relative_to(base).parts:
        cur = cur / part
        if cur.is_symlink():
            raise GitToolError(f"refused: {cur} is a symlink")


def check_folder(path: str) -> Path:
    """A folder inside an allowed root, reached without symlinks."""
    if not path or "\0" in path:
        raise GitToolError("invalid path")
    absolute = Path(os.path.abspath(os.path.expanduser(path)))
    for root in allowed_roots():
        try:
            absolute.relative_to(root)
        except ValueError:
            continue
        _no_symlinks(root, absolute)
        real = absolute.resolve()
        if not real.is_relative_to(root):
            raise GitToolError(f"refused: {path} leaves the allowed folder")
        if not real.is_dir():
            raise GitToolError(f"not a folder: {real}")
        return real
    raise GitToolError(f"{absolute} is outside the allowed folders; add it to GIT_TOOL_ALLOWED_ROOTS")


def check_repo(path: str) -> Path:
    """The folder must be a repo root with a real .git folder (no gitfile pointing elsewhere)
    and a config without hostile keys."""
    root = check_folder(path)
    git_dir = root / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise GitToolError("refused: this folder is not a repo root with a .git folder")
    cfg = git_dir / "config"
    if cfg.exists():
        p = subprocess.run(["git", "config", "--file", str(cfg), "--no-includes", "--name-only", "--list"],
                           env=clean_env(True), capture_output=True, text=True, timeout=TIMEOUT_S)
        bad = sorted({k for k in p.stdout.split() if HOSTILE_KEY.match(k)})
        if bad:
            raise GitToolError("refused: this repo's config sets keys that can run programs or "
                               f"redirect git ({', '.join(bad)}). Inspect .git/config by hand.")
    return root


def _is_git_dir_name(part: str) -> bool:
    """'.git' in every spelling that reaches the real .git folder on Windows or macOS:
    '.GIT' (case-insensitive file systems), '.git.' / '.git ' (Windows drops trailing dots and
    spaces) and 'GIT~1' (the Windows 8.3 short name)."""
    p = part.lower().rstrip(". ")
    return p == ".git" or re.fullmatch(r"git~\d+", p) is not None


def check_file_arg(repo: Path, rel: str) -> str:
    """A path argument inside the repo: relative, no '..', no leading '-', no symlinks, nothing
    inside .git (in any spelling), no Windows drive, UNC path or ':' stream."""
    if not isinstance(rel, str) or not rel or "\0" in rel:
        raise GitToolError(f"refused path argument: {rel!r}")
    norm = rel.replace("\\", "/")              # a backslash is a separator on Windows
    if norm.startswith(("-", "/", "~")) or ":" in norm or Path(norm).is_absolute() or ".." in Path(norm).parts:
        raise GitToolError(f"refused path argument: {rel!r}")
    if any(_is_git_dir_name(part) for part in Path(norm).parts):
        raise GitToolError("refused: paths inside .git")
    target = repo / norm
    _no_symlinks(repo, target)
    if not target.resolve().is_relative_to(repo):
        raise GitToolError(f"refused path argument: {rel!r}")
    return Path(norm).as_posix()


# ------------------------------------------------------------------ run

DENY_OPTIONS = ("--exec", "--upload-pack", "--receive-pack", "--config-env", "--git-dir",
                "--work-tree", "--namespace", "--super-prefix")


def run(repo: Path, args: list[str], read_only: bool, ok_codes=(0,)) -> subprocess.CompletedProcess:
    """args are built by our own code, never by the model; this is a last line of defense."""
    before_sep = args[:args.index("--")] if "--" in args else args
    if not args or args[0].startswith("-"):
        # Global options (-c key=value, -C dir, --exec-path...) would come AFTER our overrides and win.
        raise GitToolError("refused: global git options are not allowed")
    if any(a.startswith(DENY_OPTIONS) for a in before_sep) or (args[0] == "rebase" and "-x" in before_sep):
        raise GitToolError("refused option")
    try:
        p = subprocess.run(["git", *overrides(), "--no-pager", *args], cwd=repo, env=clean_env(read_only),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise GitToolError(f"git {args[0]} timed out after {TIMEOUT_S}s")
    p.stdout, p.stderr = p.stdout[:MAX_OUTPUT], p.stderr[:MAX_OUTPUT]
    if p.returncode not in ok_codes:
        raise GitToolError((p.stderr or p.stdout).strip()[:2000] or f"git {args[0]} failed ({p.returncode})")
    return p


# ------------------------------------------------------------------ local remotes

def local_remote_path(repo: Path, url: str) -> Path | None:
    """The folder a remote URL points to when it is on this machine (a path or file://), else None."""
    if url.startswith("file://"):
        return Path(url[len("file://"):])
    if re.match(r"^[A-Za-z]:[\\/]", url) or url.startswith(("/", "./", "../", "\\\\")) or (
            "://" not in url and ":" not in url):
        return (repo / url) if not Path(url).is_absolute() else Path(url)
    return None                                  # https://, ssh://, git@host:path


def check_local_remote(repo: Path, url: str) -> None:
    """Pushing to a repo on this machine runs THAT repo's hooks (pre-receive, update, post-receive...)
    as you, and git does not pass our -c overrides to it. So a local remote must be inside the
    allowed folders, have a clean config, and have no hook files (only *.sample)."""
    path = local_remote_path(repo, url)
    if path is None:
        return
    folder = check_folder(str(path))
    git_dir = folder if (folder / "HEAD").is_file() and (folder / "objects").is_dir() else folder / ".git"
    cfg = git_dir / "config"
    if cfg.exists():
        p = subprocess.run(["git", "config", "--file", str(cfg), "--no-includes", "--name-only", "--list"],
                           env=clean_env(True), capture_output=True, text=True, timeout=TIMEOUT_S)
        bad = sorted({k for k in p.stdout.split() if HOSTILE_KEY.match(k)})
        if bad:
            raise GitToolError(f"refused: the local remote's config sets keys that can run programs ({', '.join(bad)})")
    hooks = git_dir / "hooks"
    active = sorted(h.name for h in hooks.iterdir() if h.is_file() and not h.name.endswith(".sample")) if hooks.is_dir() else []
    if active:
        raise GitToolError("refused: the local remote has hooks that would run on your computer when you push "
                           f"({', '.join(active)}). Inspect them by hand first.")
