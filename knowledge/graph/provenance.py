"""Provenance store: every document and chunk the graph can point to, with its license.

Built from the SAME adapters, cleaning and chunking as ingestion, so a chunk id here is the
Qdrant point id of the same text. Written to knowledge/graph/data/ (git-ignored: it holds the
full chunk text of third-party docs). Licenses and attribution come from the corpus manifest.

  documents.json   one record per document: title, source, path, url, license, attribution,
                   collection, file type, content hash, ingestion date (pages for PDFs)
  chunks.jsonl     one record per chunk: document id, section, order, text, character span,
                   page range for PDFs

Idempotent: a document whose content hash did not change keeps its first ingestion date, and
records are written sorted, so building twice gives byte-identical files.

The hand-written troubleshooting playbook is stored as a document too (collection "playbook"),
one chunk per entry, so playbook edges have evidence like every other edge. It is never sent
to Qdrant: graph retrieval skips that collection.
"""

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from knowledge.config import load_config
from knowledge.ingest.chunk import chunk_document, chunk_id
from knowledge.ingest.parse import clean
from knowledge.sources.base import PROJECT_ROOT
from knowledge.sources.local_docs import CORPUS, all_adapters, manifest_sources

DATA = Path(__file__).resolve().parent / "data"
COLLECTIONS = ("dev_docs", "git_docs", "pdf_docs")
PLAYBOOK = PROJECT_ROOT / "knowledge" / "playbooks" / "git_errors.yaml"
PLAYBOOK_SOURCE = {
    "name": "playbook", "collection": "playbook",
    "license": "unknown (written for this project; the repository declares no license yet)",
    "attribution": "Troubleshooting playbook written for MCP-NativeAgent, knowledge/playbooks/git_errors.yaml",
}
UNKNOWN_LICENSE = "unknown (not stated in the corpus manifest)"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def title_of(doc) -> str:
    if doc.title:
        return doc.title
    m = re.search(r"^#{1,6}\s+(.+?)\s*#*$", doc.text, re.M)
    return m.group(1).strip() if m else doc.path.rsplit("/", 1)[-1]


def source_meta() -> dict[str, dict]:
    return {s["name"]: s for s in manifest_sources()} | {"playbook": PLAYBOOK_SOURCE}


def corpus_file(doc) -> str | None:
    """The file behind a document, relative to the corpus folder (what the page opener may serve)."""
    if doc.source == "playbook":
        return None
    f = CORPUS / doc.source / doc.path
    return f.relative_to(CORPUS).as_posix() if f.is_file() else None


def chunk_record(c) -> dict:
    rec = {"id": c.id, "document_id": c.doc_id, "section": c.section, "order": c.index, "text": c.text,
           "char_start": c.char_start, "char_end": c.char_end, "collection": c.collection, "source": c.source}
    if c.page_start is not None:
        rec |= {"page_start": c.page_start, "page_end": c.page_end}
    return rec


def document_record(doc, n_chunks: int, meta: dict, previous: dict | None, today: str) -> dict:
    h = sha256(doc.text)
    rec = {
        "id": doc.id, "title": title_of(doc), "source": doc.source, "path": doc.path, "url": doc.url,
        "license": meta.get("license") or UNKNOWN_LICENSE,
        "attribution": meta.get("attribution") or "",
        "collection": doc.collection, "file_type": doc.kind(), "content_hash": h,
        # same text as last time -> same date, so a rebuild changes nothing
        "ingested_at": previous["ingested_at"] if previous and previous.get("content_hash") == h else today,
        "file": corpus_file(doc), "chunks": n_chunks,
        "repo": meta.get("repo") or meta.get("url"), "commit": meta.get("commit") or meta.get("version"),
    }
    if doc.page_starts:
        rec["pages"] = len(doc.page_starts)
    return rec


# ---- playbook as a document --------------------------------------------------------------

def playbook_chunks(path: Path = PLAYBOOK) -> tuple[str, list[dict]]:
    """One chunk per repo state, concept and error entry: the entry's own YAML lines, verbatim,
    so a quote from it can be checked against the file."""
    text = path.read_text(encoding="utf-8")
    doc_id = "playbook:git_errors.yaml"
    lines = text.splitlines(keepends=True)
    offsets, pos = [], 0
    for l in lines:
        offsets.append(pos)
        pos += len(l)
    chunks, group = [], None
    entry = re.compile(r"^  ([A-Za-z0-9_]+):")
    starts = []
    for i, l in enumerate(lines):
        if re.match(r"^[a-z_]+:", l):
            group = l.split(":")[0]
        elif group in ("repo_states", "concepts", "errors") and entry.match(l):
            starts.append((i, group, entry.match(l).group(1)))
    for n, (i, group, key) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        while end > i + 1 and (not lines[end - 1].strip() or re.match(r"^[a-z_]+:", lines[end - 1])):
            end -= 1
        body = "".join(lines[i:end]).rstrip("\n")
        section = f"{group} > {key}"
        chunks.append({"id": chunk_id(doc_id, n), "document_id": doc_id, "section": section, "order": n,
                       "text": f"{section}\n\n{body}", "char_start": offsets[i],
                       "char_end": offsets[i] + len(body), "collection": "playbook", "source": "playbook",
                       "key": f"{group}/{key}"})
    return text, chunks


def playbook_document(text: str, n_chunks: int, previous: dict | None, today: str) -> dict:
    h = sha256(text)
    return {"id": "playbook:git_errors.yaml", "title": "Git troubleshooting playbook", "source": "playbook",
            "path": "knowledge/playbooks/git_errors.yaml", "url": None,
            "license": PLAYBOOK_SOURCE["license"], "attribution": PLAYBOOK_SOURCE["attribution"],
            "collection": "playbook", "file_type": "yaml", "content_hash": h,
            "ingested_at": previous["ingested_at"] if previous and previous.get("content_hash") == h else today,
            "file": None, "chunks": n_chunks, "repo": None, "commit": None}


# ---- build / load ------------------------------------------------------------------------

def build(collections=COLLECTIONS, out: Path = DATA, today: str | None = None,
          playbook: Path | None = PLAYBOOK, adapters=None,
          meta: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Read every source of the given collections and write documents.json + chunks.jsonl.
    `adapters` (collection -> adapters) and `meta` (source -> manifest entry) replace the
    corpus manifest in tests."""
    cfg = load_config().chunking
    today = today or date.today().isoformat()
    previous = {d["id"]: d for d in load_documents(out)} if (out / "documents.json").exists() else {}
    meta = meta if meta is not None else source_meta()
    docs, chunks = [], []
    for coll in collections:
        for adapter in (adapters[coll] if adapters is not None else all_adapters(coll)):
            for doc in adapter.documents(coll):
                doc.text = clean(doc.text, cfg.max_code_lines)
                cs = chunk_document(doc, cfg.max_chars, cfg.overlap_chars, cfg.min_chars)
                docs.append(document_record(doc, len(cs), meta.get(doc.source, {}), previous.get(doc.id), today))
                chunks.extend(chunk_record(c) for c in cs)
    if playbook is not None and playbook.exists():
        text, pcs = playbook_chunks(playbook)
        docs.append(playbook_document(text, len(pcs), previous.get("playbook:git_errors.yaml"), today))
        chunks.extend(pcs)
    docs.sort(key=lambda d: d["id"])
    chunks.sort(key=lambda c: (c["document_id"], c["order"]))
    out.mkdir(parents=True, exist_ok=True)
    (out / "documents.json").write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    with (out / "chunks.jsonl").open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    return docs, chunks


def load_documents(folder: Path = DATA) -> list[dict]:
    return json.loads((folder / "documents.json").read_text(encoding="utf-8"))


def load_chunks(folder: Path = DATA) -> list[dict]:
    with (folder / "chunks.jsonl").open(encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="build the provenance store (documents + chunks)")
    ap.add_argument("--collections", default=",".join(COLLECTIONS))
    a = ap.parse_args()
    docs, chunks = build(tuple(a.collections.split(",")))
    by = {}
    for d in docs:
        by.setdefault(d["collection"], [0, 0])
        by[d["collection"]][0] += 1
        by[d["collection"]][1] += d["chunks"]
    for coll, (nd, nc) in sorted(by.items()):
        print(f"  {coll:10} {nd:4} documents {nc:6} chunks")
    print(f"wrote {DATA / 'documents.json'} and chunks.jsonl")


if __name__ == "__main__":
    main()
