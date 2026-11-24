"""Adapter for documentation folders in knowledge/corpus/<source>/ (Markdown, .rst, AsciiDoc).

Source-specific cleanup lives here, never in the ingestion core:
- GitHub docs: leftover Liquid tags
- Docker docs: Hugo shortcodes {{< ... >}}
- FastAPI docs: MkDocs code includes {* ... *} and /// admonition markers
- pytest / Celery: reStructuredText headings, directives and roles -> Markdown-like text
- git docs (.adoc) / Pro Git (.asc): AsciiDoc titles, blocks, linkgit: macros -> Markdown-like text

Each manifest source names its collection; an adapter only yields documents for its own
collection, so dev_docs and git_docs never mix.
"""

import re
from pathlib import Path
from typing import Iterator

import yaml

from knowledge.sources.base import Document, assert_allowed

KNOWLEDGE = Path(__file__).resolve().parents[1]
CORPUS = KNOWLEDGE / "corpus"
MANIFEST = KNOWLEDGE / "corpus_manifest.yaml"

RST_UNDERLINE = re.compile(r"^([=\-~^*#+`'\"])\1{2,}\s*$")
RST_ROLE = re.compile(r":[\w:.-]+:`([^`<]*?)(?:\s*<[^>]*>)?`")  # :ref:`text <target>` -> text
RST_LINK = re.compile(r"`([^`<]+?)\s*<[^>]+>`_+")                  # `text <url>`_ -> text
DROP_DIRECTIVES = ("toctree", "include", "contents", "currentmodule", "module", "index",
                   "_", "versionadded", "versionchanged", "deprecated", "autoclass",
                   "automodule", "image", "figure", "only", "highlight", "testsetup")


def rst_to_md(text: str) -> str:
    lines, out, levels = text.splitlines(), [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        # Overline + title + underline: skip the overline, the title is handled next loop.
        if RST_UNDERLINE.match(line) and nxt.strip() and i + 2 < len(lines) and RST_UNDERLINE.match(lines[i + 2]):
            i += 1
            continue
        # Title followed by underline -> Markdown heading; level = order the char first appears.
        if line.strip() and RST_UNDERLINE.match(nxt) and len(nxt.strip()) >= len(line.strip()) - 2:
            ch = nxt.strip()[0]
            if ch not in levels:
                levels.append(ch)
            out.append("#" * min(levels.index(ch) + 1, 6) + " " + line.strip())
            i += 2
            continue
        m = re.match(r"^\s*\.\.\s+([\w:-]+)::\s*(.*)$", line)
        if m:
            name, arg = m.group(1), m.group(2)
            if name in ("code-block", "code", "sourcecode"):
                out.append(f"Code ({arg or 'text'}):")
            elif name in ("note", "warning", "tip", "important", "seealso", "attention"):
                out.append(f"{name.capitalize()}: {arg}".strip())
            elif name.startswith(("auto", "py:")) or name in ("function", "class", "method", "fixture", "confval", "data", "envvar", "option"):
                out.append(f"{arg}")  # keep API names, they matter for exact-match questions
            elif name.split(":")[0] not in DROP_DIRECTIVES:
                out.append(arg)
            i += 1
            continue
        if re.match(r"^\s*\.\.\s+_[\w-]+:\s*$", line):  # .. _label: link targets
            i += 1
            continue
        out.append(line)
        i += 1
    text = "\n".join(out)
    text = RST_LINK.sub(r"\1", text)
    text = RST_ROLE.sub(r"\1", text)
    return text.replace("``", "`")


ADOC_UNDERLINE = {"=": 1, "-": 2, "~": 3, "^": 4}
ADOC_DROP_LINE = re.compile(
    r"^(\[\[[^\]]*\]\]|\[[^\]]*\]|ifn?def::.*|endif::.*|include::.*|image::.*|:[\w-]+:.*|//.*)\s*$")
ADOC_DELIM = re.compile(r"^(={4,}|\*{4,}|_{4,}|\+{4,}|\|===)\s*$")


def adoc_to_md(text: str) -> str:
    lines, out, i, in_code = text.splitlines(), [], 0, False
    while i < len(lines):
        line = lines[i].rstrip()
        nxt = lines[i + 1].rstrip() if i + 1 < len(lines) else ""
        if re.match(r"^(-{4,}|\.{4,})$", line):          # listing / literal block
            out.append("```")
            in_code = not in_code
            i += 1
            continue
        if in_code:
            out.append(line)
            i += 1
            continue
        # Two-line title: "git-push(1)" over "===========" (underline about as long as the title)
        if (line.strip() and nxt and nxt[0] in ADOC_UNDERLINE and len(set(nxt)) == 1
                and abs(len(nxt) - len(line)) <= 2 and not line.startswith(("=", "-", "["))):
            out.append("#" * ADOC_UNDERLINE[nxt[0]] + " " + line.strip())
            i += 2
            continue
        m = re.match(r"^(=+)\s+(.+)$", line)               # one-line title "=== Title"
        if m:
            out.append("#" * min(len(m.group(1)), 6) + " " + m.group(2).strip())
            i += 1
            continue
        if ADOC_DELIM.match(line) or ADOC_DROP_LINE.match(line):
            i += 1
            continue
        if re.match(r"^\.[A-Za-z]", line):                   # ".Caption" block title
            out.append(line[1:])
            i += 1
            continue
        line = re.sub(r"^(\S.*?)::\s*$", r"- \1:", line)       # "term::" definition list
        out.append(line)
        i += 1
    text = "\n".join(out)
    text = re.sub(r"linkgit:([\w.-]+)\[(\d)\]", r"`\1(\2)`", text)
    text = re.sub(r"\(\(\(.*?\)\)\)", "", text)                # index terms
    text = re.sub(r"\[\[[\w.:-]+\]\]", "", text)                 # inline anchors [[_id]]
    text = re.sub(r"<<[^,>]+,\s*([^>]+)>>", r"\1", text)       # <<ref,text>> -> text
    text = re.sub(r"<<[^>]+>>", "another section", text)        # bare cross-reference ids are noise
    text = re.sub(r"\{litdd\}", "--", text)
    return text


def clean_source_specific(source: str, text: str) -> str:
    text = re.sub(r"\{%-?.*?-?%\}", "", text, flags=re.S)        # Liquid
    text = re.sub(r"\{\{[<%].*?[>%]\}\}", "", text, flags=re.S)  # Hugo shortcodes
    text = re.sub(r"\{\*.*?\*\}", "", text, flags=re.S)          # MkDocs includes
    text = re.sub(r"^///.*$", "", text, flags=re.M)              # MkDocs admonition fences
    text = re.sub(r"\s*\{ #[\w-]+ \}", "", text)                   # MkDocs heading anchors { #id }
    return text


def frontmatter_to_heading(text: str) -> str:
    """GitHub docs pages start with YAML front matter (title, intro, redirects, versions).
    Keep the title as a heading and the intro as the first paragraph; drop the rest."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    head = f"# {meta['title']}\n\n" if meta.get("title") else ""
    intro = f"{meta['intro']}\n\n" if isinstance(meta.get("intro"), str) else ""
    return head + intro + text[m.end():]


def autotitle_links(text: str) -> str:
    """[AUTOTITLE](/authentication/x/generating-a-new-ssh-key) -> 'generating a new ssh key'."""
    return re.sub(r"\[AUTOTITLE\]\(([^)#]*)[^)]*\)",
                  lambda m: m.group(1).rstrip("/").rsplit("/", 1)[-1].replace("-", " ") or "the linked page", text)


class LocalDocsAdapter:
    """One adapter for every folder listed in the corpus manifest."""

    def __init__(self, source: dict):
        self.name = source["name"]
        self.url_template = source.get("url_template")
        self.url_strip = source.get("url_strip", "")
        self.collection = source.get("collection", "dev_docs")

    def url_for(self, path: str) -> str | None:
        if not self.url_template:
            return None
        p = path.removeprefix(self.url_strip).rsplit(".", 1)[0]
        p = p.removesuffix("/index").removesuffix("/_index")
        return self.url_template.format(path_no_ext=p)

    def documents(self, collection: str) -> Iterator[Document]:
        if collection != self.collection:
            return
        root = CORPUS / self.name
        files = [f for ext in ("*.md", "*.rst", "*.adoc", "*.asc") for f in root.rglob(ext)]
        for f in sorted(files):
            assert_allowed(f)
            rel = f.relative_to(root).as_posix()
            text = f.read_text(encoding="utf-8", errors="replace")
            if f.suffix == ".rst":
                text = rst_to_md(text)
            elif f.suffix in (".adoc", ".asc"):
                text = adoc_to_md(text)
            if self.collection == "git_docs":
                # dev_docs keeps its exact text so the existing benchmark stays comparable.
                text = autotitle_links(frontmatter_to_heading(text))
            yield Document(
                id=f"{self.name}:{rel}", text=clean_source_specific(self.name, text),
                source=self.name, path=rel, url=self.url_for(rel), collection=collection,
            )


def all_adapters(collection: str | None = None) -> list[LocalDocsAdapter]:
    """Adapters for every downloaded source; only those of `collection` when given."""
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    adapters = [LocalDocsAdapter(s) for s in manifest["sources"] if (CORPUS / s["name"]).exists()]
    return [a for a in adapters if collection is None or a.collection == collection]
