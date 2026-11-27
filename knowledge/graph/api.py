"""HTTP API and viewer for the knowledge graph.

Run:   uvicorn knowledge.graph.api:app --port 8100      then open http://localhost:8100/
Build the data first: python -m knowledge.graph.provenance && python -m knowledge.graph.build

  GET /                          the viewer (self-contained HTML, vendored force-graph, no CDN)
  GET /graph                     filtered view: types, relations, collections, communities,
                                 min_confidence, has_pdf, focus, depth, max_nodes, view
  GET /graph/search?q=           autocomplete over names and aliases
  GET /graph/node/{id}           node inspector: sources, chunks, relations, used in answers
  GET /graph/edge/{id}           edge inspector: endpoints and evidence with chunk and document
  GET /graph/path?from=&to=      shortest path, each step with its evidence
  GET /graph/community/{id}      one community's members
  GET /graph/meta                counts, legend, sources with licenses
  GET /documents/{id}/chunks     every chunk of a document
  GET /documents/{id}/file       the document's file (PDF opens at #page=N), corpus folder only
  GET /answers, /answers/{run}/{qid}/trace     which chunks a benchmark answer used
  GET /chains?error=             the playbook troubleshooting chains (older view, kept)

Read-only and meant for localhost. Node ids contain ':' '/' '#', so clients URL-encode them.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from knowledge.graph import troubleshoot
from knowledge.graph.query import store

VIEWER = Path(__file__).resolve().parent / "viewer"
app = FastAPI(title="knowledge graph")


def csv(v: str | None) -> list[str] | None:
    return [x for x in v.split(",") if x] if v else None


def missing(e: Exception):
    raise HTTPException(404, f"not found: {e}")


@app.get("/", response_class=HTMLResponse)
def viewer() -> str:
    """The viewer page, with force-graph inlined so nothing loads from the network."""
    html = (VIEWER / "app.html").read_text(encoding="utf-8")
    lib = (VIEWER / "vendor" / "force-graph.min.js").read_text(encoding="utf-8")
    return html.replace("/*__FORCE_GRAPH__*/", lib)


@app.get("/graph")
def graph(types: str | None = None, relations: str | None = None, collections: str | None = None,
          communities: str | None = None, min_confidence: float = Query(0.0, ge=0, le=1), has_pdf: bool = False,
          focus: str | None = None, depth: int = Query(1, ge=0, le=3),
          max_nodes: int = Query(1500, ge=1, le=6000),
          view: str = Query("entities", pattern="^(entities|documents|communities)$")) -> dict:
    comms = [int(c) for c in csv(communities)] if communities else None
    try:
        return store().select(csv(types), csv(relations), csv(collections), comms, min_confidence, has_pdf,
                              focus, depth, max_nodes, view)
    except KeyError as e:
        missing(e)


@app.get("/graph/meta")
def meta() -> dict:
    s = store()
    return {**s.meta, "communities": [{k: c[k] for k in ("id", "label", "size", "top")}
                                      for c in s.communities.values() if c["size"] >= 2],
            "nodes": len(s.nodes), "edges": len(s.edges), "documents": len(s.docs), "chunks": len(s.chunks)}


@app.get("/graph/search")
def search(q: str = Query(..., min_length=1, max_length=120), limit: int = Query(15, ge=1, le=50)) -> list[dict]:
    return store().search(q, limit)


@app.get("/graph/node/{node_id:path}")
def node(node_id: str) -> dict:
    try:
        return store().node(node_id)
    except KeyError as e:
        missing(e)


@app.get("/graph/edge/{edge_id}")
def edge(edge_id: str) -> dict:
    try:
        return store().edge(edge_id)
    except KeyError as e:
        missing(e)


@app.get("/graph/path")
def path(from_: str = Query(..., alias="from"), to: str = Query(...)) -> dict:
    try:
        return store().path(from_, to)
    except KeyError as e:
        missing(e)


@app.get("/graph/community/{cid}")
def community(cid: int, limit: int = Query(300, ge=1, le=3000)) -> dict:
    try:
        return store().community(cid, limit)
    except KeyError as e:
        missing(e)


@app.get("/documents/{doc_id:path}/chunks")
def document_chunks(doc_id: str) -> dict:
    try:
        return store().document_chunks(doc_id)
    except KeyError as e:
        missing(e)


@app.get("/documents/{doc_id:path}/file")
def document_file(doc_id: str):
    """The original file, for 'open document' and PDF page links (#page=N is applied by the
    browser). Only files of known documents inside the corpus folder; nothing else."""
    try:
        p = store().file_for(doc_id)
    except KeyError as e:
        missing(e)
    except PermissionError:
        raise HTTPException(403, "outside the corpus folder")
    media = {".pdf": "application/pdf"}.get(p.suffix.lower(), "text/plain; charset=utf-8")
    return FileResponse(p, media_type=media, headers={"Content-Disposition": "inline"})


@app.get("/answers")
def answers() -> list[dict]:
    return store().answer_runs()


@app.get("/answers/{run}/{qid}/trace")
def trace(run: str, qid: str) -> dict:
    try:
        return store().trace(run, qid)
    except KeyError as e:
        missing(e)


@app.get("/answers/{run}")
def answer_list(run: str) -> list[dict]:
    rows = store().answers.get(run)
    if rows is None:
        raise HTTPException(404, run)
    return [{"id": r["id"], "type": r.get("type"), "question": r["question"], "verdict": r.get("verdict")}
            for r in rows]


@app.get("/chains")
def chains(error: str | None = None, collection: str = "git_docs") -> dict:
    """repo state -> error -> cause -> command -> concept -> doc section, for one error or all."""
    from knowledge.graph.export import build_graph
    g = build_graph(collection)
    if not g.get("chains"):
        raise HTTPException(404, f"no troubleshooting chains for {collection!r}")
    try:
        return troubleshoot.chain_subgraph(g, error)
    except KeyError as e:
        missing(e)
