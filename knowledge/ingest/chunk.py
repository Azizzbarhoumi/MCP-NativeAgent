"""Heading-aware chunking with overlap and stable ids. Works on any Markdown-like text."""

import re
import uuid
from bisect import bisect_right

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
    # Where the chunk's body sits in the document text (None when it cannot be located,
    # e.g. a sentence-tail overlap that was re-joined). PDFs also get 1-based page numbers.
    char_start: int | None = None
    char_end: int | None = None
    page_start: int | None = None
    page_end: int | None = None


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


def locate(text: str, piece: str, cursor: int) -> tuple[int | None, int | None]:
    """Character span of `piece` in the document text, searching from `cursor`.

    A piece is not always one substring: paragraphs are re-joined with one blank line and an
    overlap may carry re-joined sentences. Its first and last 60 characters each lie inside
    one paragraph, so they are found separately. Returns (None, None) when they are not."""
    head, tail = piece[:60], piece[-60:]
    start = text.find(head, cursor)
    if start < 0:
        start = text.find(head)
    if start < 0:
        return None, None
    end = text.find(tail, start)
    return (start, end + len(tail)) if end >= 0 else (start, start + len(piece))


def page_of(page_starts: list[int], offset: int) -> int:
    return max(bisect_right(page_starts, offset), 1)


def chunk_document(doc: Document, max_chars: int, overlap: int, min_chars: int) -> list[Chunk]:
    chunks, cursor = [], 0
    for section, body in split_sections(doc.text):
        for piece in split_body(body, max_chars, overlap):
            start, end = locate(doc.text, piece, cursor)
            if start is not None:
                cursor = start          # the next piece may overlap this one, never start before it
            if len(piece) < min_chars:
                continue
            i = len(chunks)
            title = section or doc.path
            pages = {}
            if doc.page_starts and start is not None:
                pages = {"page_start": page_of(doc.page_starts, start),
                         "page_end": page_of(doc.page_starts, max(end - 1, start))}
            chunks.append(Chunk(
                id=chunk_id(doc.id, i), doc_id=doc.id, index=i, text=f"{title}\n\n{piece}",
                section=section, source=doc.source, path=doc.path, url=doc.url,
                collection=doc.collection, char_start=start, char_end=end, **pages,
            ))
    return chunks
