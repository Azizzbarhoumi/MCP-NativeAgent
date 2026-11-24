"""Heading-aware chunking with overlap and stable ids. Works on any Markdown-like text."""

import re
import uuid

from pydantic import BaseModel

from knowledge.sources.base import Document

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
FENCE = re.compile(r"^(```|~~~)")
ID_NAMESPACE = uuid.UUID("6f1c0a52-3f0e-4b6e-9a43-6a3e0c1d2b77")  # fixed: same input -> same id


class Chunk(BaseModel):
    id: str            # uuid5(doc_id#index): stable across runs, valid Qdrant point id
    doc_id: str
    index: int
    text: str          # what gets embedded: "section title\n\nbody"
    section: str       # heading path, e.g. "Fixtures > Scope"
    source: str
    path: str
    url: str | None
    collection: str


def chunk_id(doc_id: str, index: int) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, f"{doc_id}#{index}"))


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split into (heading path, body). '#' lines inside code fences are not headings."""
    sections, path, body, in_code = [], [], [], False
    for line in text.splitlines():
        if FENCE.match(line.strip()):
            in_code = not in_code
        m = None if in_code else HEADING.match(line)
        if m:
            if "\n".join(body).strip():
                sections.append((" > ".join(path), "\n".join(body).strip()))
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2).strip()]
            body = []
        else:
            body.append(line)
    if "\n".join(body).strip():
        sections.append((" > ".join(path), "\n".join(body).strip()))
    return sections


def paragraphs(body: str) -> list[str]:
    """Split on blank lines, but never inside a fenced code block (keeps snippets whole)."""
    out, cur, in_code = [], [], False
    for line in body.splitlines():
        if FENCE.match(line.strip()):
            in_code = not in_code
        if not line.strip() and not in_code:
            if cur:
                out.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    if cur:
        out.append("\n".join(cur))
    return out


def _overlap(paras: list[str], n: int) -> list[str]:
    """Paragraphs carried into the next chunk: trailing prose up to ~n chars.
    Code blocks are never carried (cutting one in half produces garbage), and a long last
    paragraph contributes only its last sentences."""
    carried, size = [], 0
    for p in reversed(paras):
        if FENCE.match(p.lstrip()):
            break
        if size + len(p) > n:
            if not carried:  # long prose paragraph: keep its last sentences
                sentences = re.split(r"(?<=[.!?])\s+", p)
                tail = ""
                for sent in reversed(sentences):
                    if len(tail) + len(sent) > n:
                        break
                    tail = f"{sent} {tail}".strip()
                if tail:
                    carried.insert(0, tail)
            break
        carried.insert(0, p)
        size += len(p) + 2
    return carried


def split_body(body: str, max_chars: int, overlap: int) -> list[str]:
    """Fit a section into pieces of about max_chars: whole section if it fits, else by
    paragraph, else (one huge paragraph) by characters. Pieces after the first start
    with a small overlap from the previous piece, so an answer cut at a boundary survives."""
    if len(body) <= max_chars:
        return [body]
    pieces, cur = [], []
    for p in paragraphs(body):
        if len(p) > max_chars:  # one huge paragraph: flush, then slide a window over it
            if cur:
                pieces.append("\n\n".join(cur))
            step = max_chars - overlap
            pieces.extend(p[i:i + max_chars] for i in range(0, len(p) - overlap, step))
            cur = []
            continue
        if cur and sum(len(x) + 2 for x in cur) + len(p) > max_chars:
            pieces.append("\n\n".join(cur))
            cur = _overlap(cur, overlap)
        cur.append(p)
    if cur:
        pieces.append("\n\n".join(cur))
    return pieces


def chunk_document(doc: Document, max_chars: int, overlap: int, min_chars: int) -> list[Chunk]:
    chunks = []
    for section, body in split_sections(doc.text):
        for piece in split_body(body, max_chars, overlap):
            if len(piece) < min_chars:
                continue
            i = len(chunks)
            title = section or doc.path
            chunks.append(Chunk(
                id=chunk_id(doc.id, i), doc_id=doc.id, index=i, text=f"{title}\n\n{piece}",
                section=section, source=doc.source, path=doc.path, url=doc.url,
                collection=doc.collection,
            ))
    return chunks
