"""Adapter for PDF books in knowledge/corpus/<source>/*.pdf (manifest entry with `type: pdf`).

Library: pypdf (BSD, pure Python). PyMuPDF is faster and gives bounding boxes, but it is
AGPL-3.0, which would bind the whole project; bounding boxes were first on the cut list.

What it keeps:
- page numbers: Document.page_starts holds where each page begins in the text, so every
  chunk knows its pages ("p. 112-113") and the viewer can open the file at #page=112;
- structure: the PDF outline (bookmarks) becomes Markdown headings, so chunks get section
  paths like the other sources ("Git Branching > Rebasing > The Basic Rebase").
What it refuses: a PDF without a text layer (a scan) is reported and skipped. No OCR.
"""

import re
from pathlib import Path
from typing import Iterator

from knowledge.ingest.parse import clean
from knowledge.sources.base import Document, assert_allowed

PAGE = ""          # private-use character marking a page start; survives clean() untouched
DOT_LEADER = re.compile(r"(\. ){4,}")


def outline_entries(reader) -> list[tuple[int, str, int]]:
    """Flatten the bookmark tree into (level, title, page index), in reading order."""
    out = []

    def walk(items, level):
        for it in items:
            if isinstance(it, list):
                walk(it, level + 1)
                continue
            try:
                page = reader.get_destination_page_number(it)
            except Exception:              # a bookmark pointing nowhere: skip it, keep the rest
                continue
            title = " ".join(str(it.title).split())
            if title and page is not None and page >= 0:
                out.append((level, title, page))
    walk(reader.outline or [], 1)
    return out


def is_toc_page(text: str) -> bool:
    lines = [l for l in text.splitlines() if l.strip()]
    return bool(lines) and sum(bool(DOT_LEADER.search(l)) for l in lines) > len(lines) / 3


def page_text(text: str, headings: list[tuple[int, str]]) -> str:
    """One page as Markdown-like text: outline titles become headings at the line where the
    title is printed (top of the page if it is not found), page-number-only lines go, and a
    line that starts with '#' (a shell comment in an example) is indented so the chunker does
    not mistake it for a heading."""
    lines = [l for l in text.splitlines() if not l.strip().isdigit()]
    lines = [(" " + l) if l.lstrip().startswith("#") else l for l in lines]
    inserted = 0
    for level, title in headings:
        key = title.lower()
        hit = next((i for i in range(inserted, len(lines)) if lines[i].strip().lower() == key), None)
        heading = "#" * min(level, 6) + " " + title
        if hit is None:
            lines.insert(inserted, heading)
            inserted += 1
        else:
            lines[hit] = heading
            inserted = hit + 1
    return "\n".join(lines)


def pdf_document(path: Path, source: str, collection: str, url: str | None,
                 max_code_lines: int) -> Document | None:
    """The whole PDF as one Document, or None when no page has extractable text."""
    from pypdf import PdfReader                     # imported here: only PDF sources need it

    reader = PdfReader(path)
    texts = [(p.extract_text() or "") for p in reader.pages]
    if not any(t.strip() for t in texts):
        return None
    by_page: dict[int, list[tuple[int, str]]] = {}
    for level, title, page in outline_entries(reader):
        by_page.setdefault(page, []).append((level, title))
    parts = []
    for i, t in enumerate(texts):
        # strip("\n"), not strip(): the indent that keeps a "# comment" line from being read
        # as a heading must survive when that line opens the page.
        body = "" if is_toc_page(t) else page_text(t, by_page.get(i, [])).strip("\n")
        # No blank lines around an empty page: removing the marker must not leave a run of
        # newlines that a second clean() would collapse (that would shift every offset).
        parts.append(PAGE + (body + "\n\n" if body.strip() else ""))
    # Clean exactly as ingestion will (so its second pass changes nothing), then read the
    # page offsets off the markers and drop them.
    marked = clean("".join(parts), max_code_lines)
    starts, out, pos = [], [], 0
    for piece in marked.split(PAGE)[1:]:
        starts.append(pos)
        out.append(piece)
        pos += len(piece)
    title = str((reader.metadata or {}).get("/Title") or path.stem)
    return Document(id=f"{source}:{path.name}", text="".join(out), source=source, path=path.name, url=url,
                    collection=collection, title=title, file_type="pdf", page_starts=starts)


class PdfAdapter:
    """Every *.pdf in the source's corpus folder, as one Document each."""

    def __init__(self, source: dict, corpus: Path):
        self.name = source["name"]
        self.collection = source.get("collection", "pdf_docs")
        self.url = source.get("url")
        self.root = corpus / self.name

    def documents(self, collection: str) -> Iterator[Document]:
        if collection != self.collection:
            return
        from knowledge.config import load_config
        max_code = load_config().chunking.max_code_lines
        for f in sorted(self.root.glob("*.pdf")):
            assert_allowed(f)
            doc = pdf_document(f, self.name, collection, self.url, max_code)
            if doc is None:
                print(f"  {self.name}/{f.name}: no text layer (a scan?), skipped; OCR is not supported")
                continue
            yield doc
