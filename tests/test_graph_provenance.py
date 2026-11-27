"""Provenance invariants of the knowledge graph (phase 1) and entity resolution with undo.

A small corpus is built in a temp folder (two Markdown documents, a two-page PDF with an
outline, the real playbook), so these tests need neither the downloaded corpus nor Qdrant.
When knowledge/graph/data/graph.json exists, the same invariants are checked on the real graph.
"""

import json
from pathlib import Path

import pytest

from knowledge.graph import build as gb
from knowledge.graph import canon
from knowledge.graph import provenance as pv
from knowledge.sources.base import Document
from knowledge.sources.pdf_docs import PdfAdapter

MD = {
    "guide/undo.md": "# Undoing things\n\nUse `git reset --hard HEAD~1` to drop the last commit. "
                     "See git-reset(1) for the details.\n\n## Recovering\n\nIf you lost a commit, run "
                     "`git reflog` and look for it. The `--force` flag is dangerous.\n\n"
                     "```\n$ git push --force-with-lease origin main\n```\n",
    "guide/push.md": "# Pushing\n\nWhen git push fails with this output:\n\n```\n! [rejected] main -> main (fetch first)\n"
                     "error: failed to push some refs to 'origin'\n```\n\nfetch first, then rebase with "
                     "`git pull --rebase` and push again. Set GIT_TRACE=1 to debug.\n",
}
PDF_PAGES = ["Intro\nThis book explains git rebase in detail and why it rewrites commits.\n"
             "Run git push --force only when nobody else uses the branch.",
             "Undoing things\nfatal: not a git repository (or any of the parent directories): .git\n"
             "Use git reset --hard HEAD~1 to drop a commit you did not push yet."]


def make_pdf(path: Path, pages: list[str] | None, outline: list[tuple[str, int]] = ()) -> Path:
    """A minimal text PDF (Helvetica, one line per Tj), or blank pages when pages is None."""
    from pypdf import PdfReader, PdfWriter
    if pages is None:
        w = PdfWriter()
        for _ in range(2):
            w.add_blank_page(width=300, height=300)
        w.write(path)
        return path
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        ops = ["BT", "/F1 11 Tf", "40 700 Td"]
        for line in text.splitlines():
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops += [f"({esc}) Tj", "0 -16 Td"]
        ops.append("ET")
        stream = "\n".join(ops)
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)
    if outline:
        w = PdfWriter(clone_from=PdfReader(path))
        for title, page in outline:
            w.add_outline_item(title, page)
        w.write(path)
    return path


class FakeAdapter:
    name = "guide_docs"

    def documents(self, collection):
        for path, text in MD.items():
            yield Document(id=f"guide_docs:{path}", text=text, source="guide_docs", path=path,
                           url=f"https://example.org/{path}", collection=collection)


META = {"guide_docs": {"license": "MIT", "attribution": "Example guide, MIT"},
        "book": {"license": "CC-BY-4.0", "attribution": "Example book, CC BY 4.0"}}


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    (root / "book").mkdir(parents=True)
    make_pdf(root / "book" / "book.pdf", PDF_PAGES, [("Intro", 0), ("Undoing things", 1)])
    adapters = {"git_docs": [FakeAdapter()],
                "pdf_docs": [PdfAdapter({"name": "book", "collection": "pdf_docs",
                                         "url": "https://example.org/book.pdf"}, root)]}
    data = tmp_path / "data"
    pv.build(("git_docs", "pdf_docs"), out=data, today="2026-11-27", adapters=adapters, meta=META)
    return data, adapters


@pytest.fixture
def graph(corpus):
    data, _ = corpus
    return gb.build(data, with_llm=False, decisions={"undo": [], "merge": []}), data


# ---- PDF ---------------------------------------------------------------------------------

def test_pdf_chunks_keep_their_pages_and_outline_sections(corpus):
    data, _ = corpus
    docs = {d["id"]: d for d in pv.load_documents(data)}
    pdf = docs["book:book.pdf"]
    assert pdf["file_type"] == "pdf" and pdf["pages"] == 2 and pdf["license"] == "CC-BY-4.0"
    chunks = [c for c in pv.load_chunks(data) if c["document_id"] == "book:book.pdf"]
    assert {c["section"] for c in chunks} == {"Intro", "Undoing things"}
    by_section = {c["section"]: c for c in chunks}
    assert (by_section["Intro"]["page_start"], by_section["Intro"]["page_end"]) == (1, 1)
    assert (by_section["Undoing things"]["page_start"], by_section["Undoing things"]["page_end"]) == (2, 2)
    assert "fatal: not a git repository" in by_section["Undoing things"]["text"]


def test_pdf_without_text_layer_is_reported_and_skipped(tmp_path, capsys):
    (tmp_path / "scan").mkdir()
    make_pdf(tmp_path / "scan" / "scan.pdf", None)
    docs = list(PdfAdapter({"name": "scan", "collection": "pdf_docs"}, tmp_path).documents("pdf_docs"))
    assert docs == []
    assert "no text layer" in capsys.readouterr().out


def test_pdf_page_offsets_survive_a_second_clean(corpus):
    """Ingestion cleans the text again; the page offsets must still point at the same text."""
    from knowledge.config import load_config
    from knowledge.ingest.parse import clean
    _, adapters = corpus
    doc = next(adapters["pdf_docs"][0].documents("pdf_docs"))
    assert clean(doc.text, load_config().chunking.max_code_lines) == doc.text
    assert doc.text[doc.page_starts[1]:].lstrip("# ").startswith("Undoing things")


# ---- documents and chunks ----------------------------------------------------------------

def test_every_document_has_a_license_field_unknown_is_explicit(corpus, tmp_path):
    data, adapters = corpus
    for d in pv.load_documents(data):
        assert d["license"] and d["attribution"] is not None, d["id"]
        assert d["file_type"] in ("md", "pdf", "yaml") and len(d["content_hash"]) == 64
    out = tmp_path / "nometa"
    docs, _ = pv.build(("git_docs",), out=out, today="2026-11-27", adapters=adapters, meta={}, playbook=None)
    assert all(d["license"] == pv.UNKNOWN_LICENSE for d in docs)


def test_store_build_is_idempotent_and_keeps_the_first_ingestion_date(corpus):
    data, adapters = corpus
    before = {f: (data / f).read_bytes() for f in ("documents.json", "chunks.jsonl")}
    pv.build(("git_docs", "pdf_docs"), out=data, today="2026-12-01", adapters=adapters, meta=META)
    assert {f: (data / f).read_bytes() for f in before} == before


def test_a_changed_document_gets_a_new_hash_and_date(corpus):
    data, adapters = corpus
    old = {d["id"]: d for d in pv.load_documents(data)}
    MD["guide/push.md"] += "\nOne more line.\n"
    try:
        pv.build(("git_docs", "pdf_docs"), out=data, today="2026-12-01", adapters=adapters, meta=META)
    finally:
        MD["guide/push.md"] = MD["guide/push.md"].replace("\nOne more line.\n", "")
    new = {d["id"]: d for d in pv.load_documents(data)}
    assert new["guide_docs:guide/push.md"]["ingested_at"] == "2026-12-01"
    assert new["guide_docs:guide/push.md"]["content_hash"] != old["guide_docs:guide/push.md"]["content_hash"]
    assert new["guide_docs:guide/undo.md"]["ingested_at"] == "2026-11-27"


def test_chunk_ids_are_the_ingestion_ids(corpus):
    """A chunk in the store is the Qdrant point of the same text (same uuid5)."""
    from knowledge.ingest.chunk import chunk_id
    data, _ = corpus
    for c in pv.load_chunks(data):
        assert c["id"] == chunk_id(c["document_id"], c["order"])


# ---- graph invariants --------------------------------------------------------------------

def check_invariants(g: dict, chunks: dict, docs: dict) -> None:
    node_ids = {n["id"] for n in g["nodes"]}
    for n in g["nodes"]:
        assert {"id", "name", "type", "aliases", "description", "collections", "mentions"} <= n.keys(), n["id"]
        for m in n["mentions"]:
            c = chunks[m["chunk_id"]]                                     # every mention's chunk exists
            assert c["document_id"] == m["document_id"] and m["document_id"] in docs
            assert 0 <= m["start"] <= m["end"] <= len(c["text"]), (n["id"], m)
            assert m["method"] in ("rule", "llm", "playbook", "structure") and 0 < m["confidence"] <= 1
    for e in g["edges"]:
        assert e["source"] in node_ids and e["target"] in node_ids, e["id"]
        assert e["evidence"], f"edge without evidence: {e['id']} {e['relation']}"
        assert e["evidence_total"] >= len(e["evidence"]) and 0 < e["confidence"] <= 1
        for ev in e["evidence"]:
            c = chunks[ev["chunk_id"]]                                    # every evidence chunk exists
            assert c["document_id"] == ev["document_id"] and ev["document_id"] in docs
            assert len(ev["quote"].split()) <= 25, ev["quote"]
            assert ev["quote"] in c["text"], (e["id"], ev["quote"])       # verbatim


def test_every_edge_has_evidence_and_every_evidence_chunk_exists(graph):
    g, data = graph
    chunks = {c["id"]: c for c in pv.load_chunks(data)}
    docs = {d["id"]: d for d in pv.load_documents(data)}
    check_invariants(g, chunks, docs)
    rels = {e["relation"] for e in g["edges"]}
    assert {"HAS_SECTION", "MENTIONS", "HAS_OPTION", "RISK", "DOCUMENTED_IN", "CAUSED_BY", "FIXED_BY"} <= rels


def test_rule_relations_quote_the_command_line(graph):
    g, _ = graph
    by = {(e["source"], e["relation"], e["target"]): e for e in g["edges"]}
    opt = by[("command:git push", "HAS_OPTION", "flag:--force-with-lease")]
    assert any("git push --force-with-lease origin main" in ev["quote"] for ev in opt["evidence"])
    risk = by[("command:git reset", "RISK", "risk_tier:destructive")]
    assert "risk.py" in risk["evidence"][0]["note"]


def test_pdf_mentions_carry_page_numbers(graph):
    g, _ = graph
    err = next(n for n in g["nodes"] if n["id"].startswith("error:fatal: not a git repository"))
    e = next(e for e in g["edges"] if e["source"] == err["id"] and e["relation"] == "DOCUMENTED_IN")
    assert e["evidence"][0]["page"] == 2


def test_doc_error_line_joins_the_playbook_error(graph):
    """'error: failed to push some refs' printed in a doc is one line of the playbook's
    non-fast-forward message, so it lands on that node (logged as an error_line merge)."""
    g, _ = graph
    err = next(n for n in g["nodes"] if "non_fast_forward" in n["aliases"])
    assert any("failed to push some refs" in a for a in err["aliases"])
    assert any(m["rule"] == "error_line" for m in g["merge_log"])


def test_playbook_edges_quote_the_playbook_entry(graph):
    g, data = graph
    chunks = {c["id"]: c for c in pv.load_chunks(data)}
    caused = [e for e in g["edges"] if e["relation"] == "CAUSED_BY"]
    assert caused
    for e in caused:
        assert all(chunks[ev["chunk_id"]]["collection"] == "playbook" for ev in e["evidence"])


# ---- entity resolution and undo ----------------------------------------------------------

def test_manpage_names_merge_into_the_command_and_the_merge_can_be_undone(corpus):
    data, _ = corpus
    g = gb.build(data, with_llm=False, with_communities=False, decisions={"undo": [], "merge": []}, write=False)
    ids = {n["id"] for n in g["nodes"]}
    assert "command:git reset" in ids and "command:git-reset(1)" not in ids
    m = next(m for m in g["merge_log"] if m["merged"] == "command:git-reset(1)")
    assert m["rule"] == "manpage" and m["kept"] == "command:git reset"
    assert m["id"] == canon.merge_id("manpage", "command:git reset", "command:git-reset(1)")   # stable id

    undone = gb.build(data, with_llm=False, with_communities=False, decisions={"undo": [m["id"]], "merge": []},
                      write=False)
    ids = {n["id"] for n in undone["nodes"]}
    assert "command:git-reset(1)" in ids and "command:git reset" in ids
    assert m["id"] not in {x["id"] for x in undone["merge_log"]}


def test_concept_plural_and_verb_forms():
    c = canon.Canon()
    assert c.resolve("concept", "Pull requests") == ("concept", "pull request")
    assert c.resolve("concept", "Rebasing") == ("concept", "rebase")
    assert {m["rule"] for m in c.log.values()} == {"plural", "verb_form"}


def test_duplicate_rate_counts_lookalikes():
    rate, pairs = canon.duplicate_rate(["git-rebase(1)", "git rebase", "git push"])
    assert rate == pytest.approx(2 / 3) and ("git-rebase(1)", "git rebase") in pairs


# ---- the real graph, when it has been built ----------------------------------------------

REAL = pv.DATA / "graph.json"


@pytest.mark.skipif(not REAL.exists(), reason="graph not built here (python -m knowledge.graph.build)")
def test_real_graph_invariants():
    g = json.loads(REAL.read_text(encoding="utf-8"))
    chunks = {c["id"]: c for c in pv.load_chunks()}
    docs = {d["id"]: d for d in pv.load_documents()}
    check_invariants(g, chunks, docs)
    assert all(d["license"] for d in docs.values())
