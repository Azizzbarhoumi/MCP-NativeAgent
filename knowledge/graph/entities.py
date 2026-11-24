"""Technical entity extraction for documentation chunks (deterministic, no LLM)."""

import re

CODE = re.compile(r"(?<!`)`([^`\n]{2,60})`(?!`)")                      # inline `code`
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]{1,40})(?:=|\b)")          # --maxfail, --unsafe-fixes
ENV = re.compile(r"\b([A-Z][A-Z0-9]*_[A-Z0-9_]{2,40})\b")               # ACTIONS_STEP_DEBUG
DOTTED = re.compile(r"\b([a-z_][a-z0-9_]*(?:\.[a-z_][A-Za-z0-9_]*){1,4})\b")  # celery.exceptions.X
SETTING = re.compile(r"\b([a-z][a-z0-9]+(?:[_-][a-z0-9]+){1,5})\b")       # task_acks_late, timeout-minutes

# Generic words that appear as `code` everywhere; as graph hubs they would tie unrelated docs together.
STOP = {"true", "false", "none", "null", "yes", "no", "self", "foo", "bar", "baz", "main", "test",
        "e.g", "i.e", "etc", "http", "https", "www", "and", "or", "not", "from", "import", "return",
        "str", "int", "float", "dict", "list", "bool", "bytes", "path", "name", "run", "add", "app",
        "default", "version", "value", "key", "type", "file", "data", "text", "body", "error", "cmd",
        "env", "debug", "json", "args", "kwargs", "tests", "image", "build", "push", "tag", "tags",
        "include", "labels", "username", "password", "id", "url", "item", "items", "user", "request"}
FILE_EXT = {"md", "rst", "txt", "html", "png", "svg", "jpg", "com", "org", "io", "net", "dev", "html"}


def norm(s: str) -> str:
    s = s.strip().strip(":;,.()[]{}'\"").strip()
    s = re.sub(r"\(.*\)$", "", s)            # foo() -> foo
    return s.lower()


def ok(key: str) -> bool:
    if len(key) < 3 or len(key) > 60 or key in STOP or key.isdigit():
        return False
    if " " in key and len(key.split()) > 3:   # long code phrases are not entities
        return False
    if "." in key and key.rsplit(".", 1)[-1] in FILE_EXT and not key.endswith((".py", ".toml", ".yml", ".yaml", ".cfg", ".ini")):
        return False
    return True


def extract(text: str, known_settings: set[str] | None = None) -> dict[str, str]:
    """Returns {entity key: kind}. Code spans, flags, env vars and dotted names are taken anywhere;
    snake/kebab-case words only when they are known (seen as code elsewhere), because plain
    words like 'built-in' or 'long_text' would otherwise flood the graph."""
    found: dict[str, str] = {}
    for m in CODE.findall(text):
        k = norm(m)
        if ok(k):
            found.setdefault(k, "code")
    for m in FLAG.findall(text):
        found.setdefault(norm(m), "flag")
    for m in ENV.findall(text):
        k = norm(m)
        if ok(k):
            found.setdefault(k, "env")
    for m in DOTTED.findall(text):
        k = norm(m)
        if ok(k) and "_" in k or (ok(k) and k.count(".") >= 2):
            found.setdefault(k, "symbol")
    if known_settings:
        for m in SETTING.findall(text):
            k = m.lower()
            if k in known_settings:
                found.setdefault(k, "code")
    return found
