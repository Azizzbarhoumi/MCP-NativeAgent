"""HTTP endpoint for the graph viewer (and later the main UI).

Run:  uvicorn knowledge.graph.api:app --port 8100
Try:  http://localhost:8100/graph?types=source,document,entity&max_nodes=800
      http://localhost:8100/graph?focus=entity:continue-on-error&depth=2
      http://localhost:8100/chains?error=merge_conflict      (git_docs troubleshooting chain)
"""

from fastapi import FastAPI, HTTPException, Query

from knowledge.graph import troubleshoot
from knowledge.graph.export import DEFAULT_TYPES, TYPES, build_graph, select

app = FastAPI(title="knowledge graph")


@app.get("/graph")
def graph(collection: str | None = None,
          types: str = Query(",".join(DEFAULT_TYPES), description=f"comma list of {TYPES}"),
          max_nodes: int = Query(1500, ge=1, le=20000),
          focus: str | None = None, depth: int = Query(1, ge=0, le=4)) -> dict:
    node_types = tuple(t for t in types.split(",") if t in TYPES + troubleshoot.CHAIN_TYPES)
    try:
        return select(build_graph(collection), node_types, max_nodes, focus, depth)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/chains")
def chains(error: str | None = None, collection: str = "git_docs") -> dict:
    """repo state -> error -> cause -> command -> concept -> doc section, for one error or all."""
    g = build_graph(collection)
    if not g.get("chains"):
        raise HTTPException(404, f"no troubleshooting chains for {collection!r}")
    try:
        return troubleshoot.chain_subgraph(g, error)
    except KeyError as e:
        raise HTTPException(404, str(e))
