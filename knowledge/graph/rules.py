"""Rule-based extraction with character spans: entities, error messages, command lines.

No LLM. Everything found here is a verbatim span of a chunk, so the inspector can highlight it
and every edge built from it can quote the line it came from.

  mentions(text)        -> [Mention(type, key, start, end, confidence)]
  command_lines(text)   -> [(command, flags, line_start, line_end)] for HAS_OPTION and RISK
  quote(text, s, e)     -> a verbatim excerpt of at most 25 words around [s, e)
"""

import re
from dataclasses import dataclass

from knowledge.graph.entities import CODE, DOTTED, ENV, FLAG, SETTING, norm, ok

# Porcelain and common plumbing commands. A word after "git" counts as a command only when it
# is in this list, so prose like "git is fast" or "git repository" never becomes a command.
GIT_SUBCOMMANDS = set("""
add am annotate apply archive bisect blame branch bundle cat-file check-ignore checkout cherry cherry-pick
citool clean clone commit commit-tree config count-objects credential credential-cache credential-store
daemon describe diff diff-tree difftool fetch filter-branch filter-repo format-patch fsck gc grep gui
hash-object help hook imap-send init instaweb lfs log ls-files ls-remote ls-tree maintenance merge
merge-base mergetool mv notes p4 prune pull push range-diff read-tree rebase reflog remote repack replace
request-pull rerere reset restore rev-list rev-parse revert rm send-email shortlog show show-ref
sparse-checkout stash status submodule svn switch symbolic-ref tag update-index update-ref var
verify-commit verify-pack verify-tag whatchanged worktree write-tree
""".split())
DOCKER_SUBCOMMANDS = set("build buildx compose container exec image images login logs ps pull push run tag "
                         "volume network system init scout".split())

COMMAND = re.compile(r"(?<![\w/.-])(git|docker)[ \t]+([a-z][a-z0-9-]*)")
MANPAGE = re.compile(r"(?<![\w/-])git-([a-z][a-z0-9-]*)(?:\(\d\))?")
# Error lines: what a beginner pastes. "hint:" and "warning:" are advice, plain "remote:" is
# progress output, so they are left out unless the remote line itself reports an error.
ERROR_LINE = re.compile(
    r"(?m)^[ \t>$|]*("
    r"(?:error|fatal|ERROR|FATAL): [^\n]{6,200}"
    r"|remote: (?:error|fatal|Permission|Support for password|Invalid username)[^\n]{3,200}"
    r"|! \[(?:rejected|remote rejected)\][^\n]{3,200}"
    r"|CONFLICT \([^\n]{3,200}"
    r"|[^\n]{0,60}Permission denied \(publickey\)[^\n]{0,80}"
    r"|You are in 'detached HEAD' state[^\n]{0,160}"
    r"|Updates were rejected because[^\n]{3,200}"
    r")\s*$")
CODE_LINE = re.compile(r"^\s*(?:\$|>|PS>|C:\\>)\s+(.*)$")


@dataclass(frozen=True)
class Mention:
    type: str          # command, flag, env, code, symbol, setting, error
    key: str           # raw key as extracted (canon.py resolves it to a node)
    start: int
    end: int
    confidence: float


CONFIDENCE = {"command": 0.95, "error": 0.9, "flag": 0.9, "env": 0.9, "code": 0.8, "symbol": 0.75,
              "setting": 0.7}


def error_key(line: str) -> str:
    """Canonical error text: variable parts (quoted names, hashes, branch arrows) replaced, so the
    same error from two pages becomes one node."""
    s = line.strip().rstrip(".").strip()
    s = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`", "<x>", s)
    s = re.sub(r"\b[0-9a-f]{7,40}\b(\.\.\.)?", "<sha>", s)
    s = re.sub(r"\] [^\s]+ -> [^\s]+", "] <ref> -> <ref>", s)
    s = re.sub(r"(?<=\bin )[\w./-]+\.\w+", "<file>", s)          # Merge conflict in app.py
    s = re.sub(r"\s+", " ", s).lower()
    if len(s) > 90:
        s = s[:90].rsplit(" ", 1)[0]
    return s


def command_key(tool: str, sub: str) -> str | None:
    known = GIT_SUBCOMMANDS if tool == "git" else DOCKER_SUBCOMMANDS
    return f"{tool} {sub}" if sub in known else None


def code_spans(text: str) -> list[tuple[int, int]]:
    """[start, end) of fenced code blocks, inline `code` and "$ prompt" lines: where a command
    is written to be run, as opposed to talked about."""
    spans, in_code, pos = [], False, 0
    block = None
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            if in_code:
                spans.append((block, pos + len(line)))
            else:
                block = pos
            in_code = not in_code
        elif not in_code and CODE_LINE.match(line):
            spans.append((pos, pos + len(line)))
        pos += len(line)
    if in_code and block is not None:
        spans.append((block, pos))
    spans += [(m.start(), m.end()) for m in CODE.finditer(text)]
    return spans


def inside(spans, s: int) -> bool:
    return any(a <= s < b for a, b in spans)


def mentions(text: str, known_settings: set[str] | None = None, skip_before: int = 0) -> list[Mention]:
    """First mention of each entity in a chunk, with its span. `skip_before` ignores the
    section-title prefix that every chunk of a section repeats."""
    found: dict[tuple[str, str], Mention] = {}

    def add(t, k, s, e):
        if s >= skip_before and (t, k) not in found:
            found[(t, k)] = Mention(t, k, s, e, CONFIDENCE[t])

    for m in ERROR_LINE.finditer(text):
        k = error_key(m.group(1))
        if len(k.split()) >= 3:
            add("error", k, m.start(1), m.end(1))
    command_spans = []
    for m in COMMAND.finditer(text):
        k = command_key(m.group(1), m.group(2))
        if k:
            add("command", k, m.start(), m.end())
            command_spans.append((m.start(), m.end()))
    for m in MANPAGE.finditer(text):
        k = command_key("git", m.group(1))
        if k:
            add("command", m.group(0).lower(), m.start(), m.end())   # canon.py folds git-x(1) into git x
    for m in CODE.finditer(text):
        raw = m.group(1).strip().lower()
        k = norm(m.group(1))
        # A code span that is a command line or a man page name is the command (and its
        # flags), found above; it must not also become a separate "code" entity.
        if not ok(k) or COMMAND.match(raw) or MANPAGE.fullmatch(raw):
            continue
        # One string, one type: a span that is a flag, an env var or a dotted symbol is found
        # by its own pattern below; "code" is only the fallback for everything else.
        span = m.group(1).strip()
        if FLAG.fullmatch(span) or ENV.fullmatch(span) or (DOTTED.fullmatch(k) and ("_" in k or k.count(".") >= 2)):
            continue
        add("code", k, m.start(1), m.end(1))
    for m in FLAG.finditer(text):
        add("flag", norm(m.group(1)), m.start(1), m.end(1))
    for m in ENV.finditer(text):
        k = norm(m.group(1))
        if ok(k):
            add("env", m.group(1), m.start(1), m.end(1))           # env vars keep their case
    for m in DOTTED.finditer(text):
        k = norm(m.group(1))
        if ok(k) and ("_" in k or k.count(".") >= 2):
            add("symbol", k, m.start(1), m.end(1))
    if known_settings:
        for m in SETTING.finditer(text):
            k = m.group(1).lower()
            if k in known_settings and ("code", k) not in found:
                add("code", k, m.start(1), m.end(1))        # a known `code` name, written in prose
    return list(found.values())


def command_lines(text: str) -> list[tuple[str, str, list[str], int, int]]:
    """Commands written to be run, with their flags: (command key, command line, flags,
    line start, line end). Only inside code (fences, `spans`, "$ " lines)."""
    spans = code_spans(text)
    out = []
    for m in COMMAND.finditer(text):
        k = command_key(m.group(1), m.group(2))
        if not k or not inside(spans, m.start()):
            continue
        ls = text.rfind("\n", 0, m.start()) + 1
        le = text.find("\n", m.end())
        le = len(text) if le < 0 else le
        # the command runs to the end of the line, or of the `code span` it sits in
        span_end = next((b for a, b in spans if a <= m.start() < b and b - a < le - ls), le)
        line = text[m.start():min(le, span_end)].strip().rstrip("`").strip()
        line = re.split(r"\s+(?:#|&&|\|\||;)\s*", line)[0]           # first command of a chain
        flags = [norm(f) for f in FLAG.findall(line)]
        out.append((k, line, flags, ls, le))
    return out


WORD = re.compile(r"\S+")


def quote(text: str, start: int, end: int, max_words: int = 25) -> str:
    """Verbatim excerpt (a slice of `text`) of at most max_words words: the line holding the
    span, or a window of words centred on it when the line is longer."""
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    le = len(text) if le < 0 else le
    words = [(m.start() + ls, m.end() + ls) for m in WORD.finditer(text[ls:le])]
    if not words:
        return text[start:end].strip()
    if len(words) <= max_words:
        return text[words[0][0]:words[-1][1]]
    centre = next((i for i, (a, b) in enumerate(words) if b > start), 0)
    lo = max(0, min(centre - max_words // 2, len(words) - max_words))
    hi = lo + max_words
    return text[words[lo][0]:words[hi - 1][1]]


def sentence(text: str, start: int, max_words: int = 30) -> str:
    """The sentence holding `start`, cut to max_words: a short description for a node."""
    a = max(text.rfind(". ", 0, start), text.rfind("\n\n", 0, start))
    a = 0 if a < 0 else a + 2
    b = text.find(". ", start)
    b = len(text) if b < 0 else b + 1
    words = " ".join(text[a:b].split()).split(" ")
    return " ".join(words[:max_words]) + (" ..." if len(words) > max_words else "")
