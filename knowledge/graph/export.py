"""Knowledge-base graph for the viewer: what contains what, and which entities each part mentions.

No LLM and no Neo4j: built from the SAME adapters and chunking as ingestion (so chunk ids
match Qdrant), plus the deterministic entity extractor in entities.py.

Node types: source, document, section, chunk, entity
Edges:      source -CONTAINS-> document -HAS_SECTION-> section -HAS_CHUNK-> chunk -MENTIONS-> entity
            document -MENTIONS-> entity (aggregated, so the default view needs no chunks)

Usage:
  python -m knowledge.graph.export                      writes knowledge/graph/viewer/knowledge_graph.html
  python -m knowledge.graph.export --json graph.json    also writes the raw nodes/edges
"""

import argparse
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from knowledge.config import load_config
from knowledge.graph.entities import extract
from knowledge.ingest.chunk import chunk_document
from knowledge.ingest.parse import clean
from knowledge.sources.local_docs import all_adapters

VIEWER = Path(__file__).resolve().parent / "viewer"
TYPES = ("source", "document", "section", "chunk", "entity")
DEFAULT_TYPES = ("source", "document", "entity")   # readable default: no sections/chunks
PREVIEW = 240


def _preview(text: str) -> str:
    t = " ".join(text.split())
    return t[:PREVIEW] + ("..." if len(t) > PREVIEW else "")


@lru_cache
def build_full(collection: str | None = None) -> dict:
    """Every node and edge for one collection. Cached: built once per process."""
    cfg = load_config()
    collection = collection or cfg.collection
    ch = cfg.chunking
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}

    def node(nid, label, ntype, **extra):
        nodes.setdefault(nid, {"id": nid, "label": label, "type": ntype, "collection": collection, **extra})

    def edge(a, b, rel):
        e = edges.setdefault((a, b, rel), {"source": a, "target": b, "relation": rel, "weight": 0})
        e["weight"] += 1

    docs = []
    for adapter in all_adapters(collection):
        for doc in adapter.documents(collection):
            doc.text = clean(doc.text, ch.max_code_lines)
            docs.append((adapter.name, doc, chunk_document(doc, ch.max_chars, ch.overlap_chars, ch.min_chars)))

    # Pass 1: names seen as `code` anywhere, so snake/kebab-case settings are recognised in prose too.
    known = {k for _, _, chunks in docs for c in chunks for k, kind in extract(c.text).items() if kind == "code"}

    for source, doc, chunks in docs:
        sid, did = f"source:{source}", f"doc:{doc.id}"
        node(sid, source, "source")
        node(did, doc.path.rsplit("/", 1)[-1], "document", source=source, path=doc.path, url=doc.url,
             preview=_preview(doc.text))
        edge(sid, did, "CONTAINS")
        for c in chunks:
            sec = c.section or "(top)"
            secid = f"section:{doc.id}#{sec}"
            node(secid, sec, "section", source=source, path=doc.path, url=doc.url, section=sec)
            edge(did, secid, "HAS_SECTION")
            cid = f"chunk:{c.id}"
            node(cid, f"{doc.path.rsplit('/', 1)[-1]} #{c.index}", "chunk", source=source, path=doc.path,
                 url=doc.url, section=sec, preview=_preview(c.text))
            edge(secid, cid, "HAS_CHUNK")
            for key, kind in extract(c.text, known).items():
                eid = f"entity:{key}"
                node(eid, key, "entity", kind=kind)
                edge(cid, eid, "MENTIONS")
                edge(did, eid, "MENTIONS")

    # Degree = number of distinct neighbours; for entities we also keep how many documents use them.
    nbrs = defaultdict(set)
    for (a, b, _rel) in edges:
        nbrs[a].add(b)
        nbrs[b].add(a)
    for nid, n in nodes.items():
        n["degree"] = len(nbrs[nid])
        if n["type"] == "entity":
            n["docs"] = sum(1 for x in nbrs[nid] if x.startswith("doc:"))
            n["sources"] = len({nodes[x]["source"] for x in nbrs[nid] if x.startswith("doc:")})
    return {"collection": collection, "nodes": list(nodes.values()), "edges": list(edges.values())}


def select(graph: dict, node_types=DEFAULT_TYPES, max_nodes: int = 1500,
           focus: str | None = None, depth: int = 1) -> dict:
    """The subgraph a viewer should draw.

    Without focus: all sources and documents of the chosen types, then the most connected
    other nodes until max_nodes; entities used by a single document are left out.
    With focus: the focus node plus everything within `depth` hops, of any type.
    """
    by_id = {n["id"]: n for n in graph["nodes"]}
    adj = defaultdict(set)
    for e in graph["edges"]:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])

    if focus:
        if focus not in by_id:
            raise KeyError(f"unknown node {focus!r}")
        keep, frontier = {focus}, {focus}
        for _ in range(max(depth, 0)):
            frontier = {m for f in frontier for m in adj[f]} - keep
            # Most connected neighbours first when the cap would cut the ring.
            for m in sorted(frontier, key=lambda x: -by_id[x]["degree"]):
                if len(keep) >= max_nodes:
                    break
                keep.add(m)
    else:
        types = set(node_types)
        fixed = [n for n in graph["nodes"] if n["type"] in types & {"source", "document"}]
        rest = [n for n in graph["nodes"] if n["type"] in types - {"source", "document"}]
        # Entities used by one document only are leaves: left out of the overview (they make
        # hairballs) and reached through focus=<document id> instead.
        rest = [n for n in rest if n["type"] != "entity" or n.get("docs", 0) >= 2]
        rest.sort(key=lambda n: -n["degree"])
        keep = {n["id"] for n in fixed[:max_nodes]}
        for n in rest:
            if len(keep) >= max_nodes:
                break
            keep.add(n["id"])

    nodes = [by_id[i] for i in keep]
    edges = [e for e in graph["edges"] if e["source"] in keep and e["target"] in keep]
    return {"collection": graph["collection"], "nodes": nodes, "edges": edges}


def write_viewer(graph: dict, out: Path) -> Path:
    """One standalone HTML file: viewer code + vendored force-graph + the data, no network."""
    template = (VIEWER / "template.html").read_text(encoding="utf-8")
    lib = (VIEWER / "vendor" / "force-graph.min.js").read_text(encoding="utf-8")
    data = json.dumps(graph, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = template.replace("/*__FORCE_GRAPH__*/", lib).replace("__GRAPH_DATA__", data)
    out.write_text(html, encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default=None)
    ap.add_argument("--out", default=str(VIEWER / "knowledge_graph.html"))
    ap.add_argument("--json", help="also write the raw graph JSON here")
    a = ap.parse_args()
    g = build_full(a.collection)
    counts = defaultdict(int)
    for n in g["nodes"]:
        counts[n["type"]] += 1
    print(f"{len(g['nodes'])} nodes {dict(counts)}, {len(g['edges'])} edges")
    if a.json:
        Path(a.json).write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    print(f"viewer: {write_viewer(g, Path(a.out))}")


if __name__ == "__main__":
    main()
