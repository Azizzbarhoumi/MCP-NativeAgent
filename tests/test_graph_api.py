"""The graph API (knowledge/graph/api.py) on a small graph built in a temp folder.

Same tiny corpus as test_graph_provenance (two Markdown documents, a two-page PDF, the real
playbook), plus a fake benchmark result file for the answer trace. No Qdrant, no network.
"""

import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from knowledge.graph import api
from knowledge.graph import build as gb
from knowledge.graph import query
from tests.test_graph_provenance import corpus  # noqa: F401  (fixture)


@pytest.fixture
def client(corpus, tmp_path, monkeypatch):  # noqa: F811
    data, _ = corpus
    gb.build(data, with_llm=False, decisions={"undo": [], "merge": []}, write=True)
    # the PDF is the only document with a file; point it at the temp corpus the fixture made
    root = tmp_path / "corpus"
    docs = json.loads((data / "documents.json").read_text(encoding="utf-8"))
    for d in docs:
        if d["source"] == "book":
            d["file"] = "book/book.pdf"
    # a tampered record: the store must still refuse to serve anything outside the corpus
    docs.append({**docs[0], "id": "evil:passwd", "file": "../../../../etc/passwd", "chunks": 0})
    (data / "documents.json").write_text(json.dumps(docs), encoding="utf-8")
    chunks = query.load_chunks(data)
    pdf_chunk = next(c for c in chunks if c["source"] == "book")
    results = tmp_path / "results"
    results.mkdir()
    (results / "knowledge_test_run.json").write_text(json.dumps({"rows": [{
        "id": "q1", "type": "lookup", "question": "How do I undo a commit with git reset?",
        "answer": "Use git reset --hard HEAD~1.", "verdict": "correct", "cited": [1],
        "retrieved": [{"chunk_id": pdf_chunk["id"], "doc": "book/book.pdf", "section": pdf_chunk["section"],
                       "score": 0.9},
                      {"chunk_id": "not-in-graph", "doc": "elsewhere.md", "section": None, "score": 0.1}]}]}))
    (results / "knowledge_test_run_partial.json").write_text("{}")       # ignored by name
    st = query.Store(data, results)
    monkeypatch.setattr(api, "store", lambda: st)
    monkeypatch.setattr(query, "CORPUS", root)
    return TestClient(api.app), st


def enc(node_id: str) -> str:
    return quote(node_id, safe="")


def test_meta_counts_match_the_store(client):
    c, st = client
    m = c.get("/graph/meta").json()
    assert m["nodes"] == len(st.nodes) and m["edges"] == len(st.edges) and m["documents"] == len(st.docs)


def test_graph_filters_and_every_edge_has_both_ends(client):
    c, _ = client
    for view in ("entities", "documents", "communities"):
        r = c.get(f"/graph?view={view}")
        assert r.status_code == 200, view
        g = r.json()
        ids = {n["id"] for n in g["nodes"]}
        assert all(e["source"] in ids and e["target"] in ids for e in g["edges"]), view
    g = c.get("/graph?types=command").json()
    assert g["nodes"] and all(n["type"] == "command" for n in g["nodes"])
    assert c.get("/graph?view=nonsense").status_code == 422
    assert c.get("/graph?depth=9").status_code == 422


def test_focus_returns_the_neighbourhood_and_unknown_focus_is_404(client):
    c, _ = client
    g = c.get(f"/graph?focus={enc('command:git reset')}&depth=1").json()
    assert "command:git reset" in {n["id"] for n in g["nodes"]} and g["edges"]
    assert c.get(f"/graph?focus={enc('command:no such thing')}").status_code == 404


def test_search_finds_commands_by_name(client):
    c, _ = client
    hits = c.get("/graph/search?q=git res").json()
    assert any(h["id"] == "command:git reset" for h in hits)
    assert c.get("/graph/search?q=").status_code == 422


def test_node_inspector_lists_sources_with_license_and_pages(client):
    c, _ = client
    n = c.get(f"/graph/node/{enc('command:git reset')}").json()
    assert n["id"] == "command:git reset"
    blob = json.dumps(n)
    assert "MIT" in blob and "CC-BY-4.0" in blob                       # both sources, with licenses
    assert c.get(f"/graph/node/{enc('command:nope')}").status_code == 404


def test_edge_inspector_shows_verbatim_evidence(client):
    c, st = client
    eid = next(e["id"] for e in st.edges.values() if e["relation"] == "MENTIONS")
    e = c.get(f"/graph/edge/{eid}").json()
    assert e["evidence"]
    for ev in e["evidence"]:
        assert ev["quote"] in st.chunks[ev["chunk_id"]]["text"]
    assert c.get("/graph/edge/missing").status_code == 404


def test_path_steps_are_edges_that_chain(client):
    c, _ = client
    p = c.get("/graph/path", params={"from": "command:git reset", "to": "command:git push"}).json()
    assert p["edges"], p
    walk = {"command:git reset"}
    for e in p["edges"]:                    # each step touches the node reached so far
        assert e["source"] in walk or e["target"] in walk
        walk |= {e["source"], e["target"]}
    assert "command:git push" in walk
    assert c.get("/graph/path", params={"from": "command:git reset", "to": "x:y"}).status_code == 404


def test_community_endpoint(client):
    c, st = client
    if not st.communities:
        pytest.skip("no communities in the small graph")
    cid = next(iter(st.communities))
    r = c.get(f"/graph/community/{cid}").json()
    assert r["nodes"] and all(n["id"] in st.nodes for n in r["nodes"])
    assert c.get("/graph/community/99999").status_code == 404


def test_document_chunks_in_order_with_pages(client):
    c, st = client
    doc_id = next(d for d, v in st.docs.items() if v["source"] == "book")
    r = c.get(f"/documents/{enc(doc_id)}/chunks").json()
    orders = [ch["order"] for ch in r["chunks"]]
    assert orders == sorted(orders) and all(ch["page_start"] for ch in r["chunks"])
    assert c.get("/documents/nope/chunks").status_code == 404


def test_pdf_opener_serves_the_file_inline(client):
    c, st = client
    doc_id = next(d for d, v in st.docs.items() if v["source"] == "book")
    r = c.get(f"/documents/{enc(doc_id)}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and "inline" in r.headers["content-disposition"]


def test_pdf_opener_refuses_traversal(client):
    c, st = client
    # a path as the id is not a known document
    for bad in ("../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", "/etc/passwd"):
        assert c.get(f"/documents/{bad}/file").status_code == 404, bad
    # a known document whose recorded file points outside the corpus: refused, not served
    r = c.get(f"/documents/{enc('evil:passwd')}/file")
    assert r.status_code == 403
    with pytest.raises(PermissionError):
        st.file_for("evil:passwd")
    # a document with no file (Markdown here, the playbook in real data) has nothing to open
    md = next(d for d, v in st.docs.items() if v["source"] == "guide_docs")
    assert c.get(f"/documents/{enc(md)}/file").status_code == 404


def test_answer_trace_maps_retrieved_chunks_to_sections_documents_and_entities(client):
    c, st = client
    runs = c.get("/answers").json()
    assert runs == [{"run": "knowledge_test_run", "questions": 1}]        # partial file ignored
    assert c.get("/answers/knowledge_test_run").json()[0]["id"] == "q1"
    t = c.get("/answers/knowledge_test_run/q1/trace").json()
    first, second = t["steps"]
    assert first["cited"] and first["in_graph"]
    assert first["document_node"] in st.nodes and first["section_node"] in st.nodes
    assert first["entities"]                     # each listed entity is mentioned in that very chunk
    for nid in first["entities"]:
        assert any(m["chunk_id"] == first["chunk_id"] for m in st.nodes[nid]["mentions"]), nid
    assert not second["cited"] and not second["in_graph"]               # unknown chunk is reported, not dropped
    ids = {n["id"] for n in t["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in t["edges"])
    assert c.get("/answers/knowledge_test_run/q9/trace").status_code == 404
    assert c.get("/answers/nope").status_code == 404


def test_viewer_is_self_contained(client):
    c, _ = client
    html = c.get("/").text
    assert "/*__FORCE_GRAPH__*/" not in html and "ForceGraph" in html
    assert "<script src=\"http" not in html and "cdn" not in html.lower()
