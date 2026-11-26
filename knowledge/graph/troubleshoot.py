"""Troubleshooting chains for the graph viewer, from the hand-written playbook.

  repo_state -LEADS_TO-> error -HAS_CAUSE-> cause -FIXED_BY-> command -USES-> concept
  concept -EXPLAINED_IN-> section (or document) of the git_docs collection

No LLM, no Neo4j, no eval data: the only input is knowledge/playbooks/git_errors.yaml plus the
section nodes export.py already builds from the corpus. Commands get their risk tier from
mcp_servers/git_local/risk.py, the same rules the assistant uses, so the two never disagree.
"""

from pathlib import Path

import yaml

from mcp_servers.git_local.risk import classify

PLAYBOOK = Path(__file__).resolve().parents[1] / "playbooks" / "git_errors.yaml"
CHAIN_TYPES = ("repo_state", "error", "cause", "command", "concept")
# Left-to-right order of the chain; the viewer draws one column per level.
LEVEL = {"repo_state": 0, "error": 1, "cause": 2, "command": 3, "concept": 4, "section": 5, "document": 5}


def load(path: Path = PLAYBOOK) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def resolve_doc(ref: str, sections: list[dict], documents: list[dict]) -> dict | None:
    """'<path fragment>#<heading fragment>' -> the first matching section node (shortest
    heading path first, so 'Basic Merge Conflicts' beats a deeper sub-heading);
    '<path fragment>' -> the document node. None when the corpus has no match."""
    frag, _, heading = ref.partition("#")
    if heading:
        h = heading.lower()
        hits = [s for s in sections if frag in s["path"] and h in s["section"].lower()]
        return min(hits, key=lambda s: (s["section"].count(">"), len(s["section"]))) if hits else None
    hits = [d for d in documents if frag in d["path"]]
    return min(hits, key=lambda d: len(d["path"])) if hits else None


def build_chains(doc_graph: dict | None = None, playbook: dict | None = None) -> dict:
    """Chain nodes and edges. With doc_graph (from export.build_full), concepts link to its
    section/document nodes; unresolved links are listed under 'unresolved', never invented."""
    pb = playbook or load()
    coll = pb.get("collection", "git_docs")
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}

    def node(nid, label, ntype, **extra):
        nodes.setdefault(nid, {"id": nid, "label": label, "type": ntype, "collection": coll,
                               "level": LEVEL[ntype], **extra})

    def edge(a, b, rel, **extra):
        return edges.setdefault((a, b, rel), {"source": a, "target": b, "relation": rel, "weight": 1, **extra})

    for sid, s in pb["repo_states"].items():
        node(f"repo_state:{sid}", s["label"], "repo_state", preview=s.get("detail", ""))
    for cid, c in pb["concepts"].items():
        node(f"concept:{cid}", c["label"], "concept", docs=c.get("docs", []))

    for eid, e in pb["errors"].items():
        err = f"error:{eid}"
        node(err, e["message"].split(". ")[0][:70], "error", preview=e["message"], chain=eid)
        for s in e.get("states", []):
            edge(f"repo_state:{s}", err, "LEADS_TO")
        for i, cause in enumerate(e["causes"]):
            cid = f"cause:{eid}/{i}"
            node(cid, cause["text"], "cause", preview=cause.get("note", ""), chain=eid)
            edge(err, cid, "HAS_CAUSE")
            for order, step in enumerate(cause["commands"], 1):
                manual = bool(step.get("manual"))
                if manual:
                    tier, reason = "manual", "done by you, outside git"
                else:
                    r = classify(step["cmd"])
                    tier, reason = r["tier"], r["reason"]
                kid = f"command:{step['cmd']}"
                node(kid, step["cmd"], "command", tier=tier, reason=reason, manual=manual)
                edge(cid, kid, "FIXED_BY", order=order)
                for c in step.get("concepts", []):
                    # Commands are shared between errors ('git status'), so each USES edge
                    # remembers which chains declared it; one error's view follows only its own.
                    chains = edge(kid, f"concept:{c}", "USES", chains=[])["chains"]
                    if eid not in chains:
                        chains.append(eid)

    unresolved = []
    if doc_graph:
        sections = [n for n in doc_graph["nodes"] if n["type"] == "section"]
        documents = [n for n in doc_graph["nodes"] if n["type"] == "document"]
        for cid, c in pb["concepts"].items():
            for ref in c.get("docs", []):
                target = resolve_doc(ref, sections, documents)
                if target is None:
                    unresolved.append(f"{cid}: {ref}")
                else:
                    edge(f"concept:{cid}", target["id"], "EXPLAINED_IN")
    return {"collection": coll, "nodes": list(nodes.values()), "edges": list(edges.values()),
            "unresolved": unresolved}


def merge(doc_graph: dict, chains: dict) -> dict:
    """Doc graph + chains in one payload for the viewer. Linked sections/documents get a level
    so the chain view can place them in the last column."""
    linked = {e["target"] for e in chains["edges"] if e["relation"] == "EXPLAINED_IN"}
    nodes = []
    for n in doc_graph["nodes"]:
        nodes.append({**n, "level": LEVEL[n["type"]]} if n["id"] in linked else n)
    nodes += chains["nodes"]
    edges = doc_graph["edges"] + chains["edges"]
    # Degree for the chain nodes (the doc graph already has its own).
    nbrs: dict[str, set] = {}
    for e in chains["edges"]:
        nbrs.setdefault(e["source"], set()).add(e["target"])
        nbrs.setdefault(e["target"], set()).add(e["source"])
    for n in chains["nodes"]:
        n["degree"] = len(nbrs.get(n["id"], ()))
    return {"collection": doc_graph["collection"], "nodes": nodes, "edges": edges,
            "chains": sorted(n["chain"] for n in chains["nodes"] if n["type"] == "error")}


def chain_subgraph(graph: dict, error_id: str | None = None) -> dict:
    """Only the troubleshooting part: chain nodes plus the doc sections they point to.
    With error_id ('merge_conflict'), only that error's chain (its states upstream,
    everything reachable downstream)."""
    out_edges: dict[str, list[str]] = {}
    in_edges: dict[str, list[str]] = {}
    chain_rel = {"LEADS_TO", "HAS_CAUSE", "FIXED_BY", "USES", "EXPLAINED_IN"}
    for e in graph["edges"]:
        if e["relation"] not in chain_rel:
            continue
        if error_id and e["relation"] == "USES" and error_id not in e["chains"]:
            continue                       # a shared command's concept from another error
        out_edges.setdefault(e["source"], []).append(e["target"])
        in_edges.setdefault(e["target"], []).append(e["source"])
    by_id = {n["id"]: n for n in graph["nodes"]}
    if error_id is None:
        keep = {n["id"] for n in graph["nodes"] if n["type"] in CHAIN_TYPES}
        keep |= {t for k in list(keep) for t in out_edges.get(k, [])}
    else:
        start = f"error:{error_id}"
        if start not in by_id:
            raise KeyError(f"unknown error {error_id!r}")
        keep, stack = {start}, [start]
        while stack:
            for t in out_edges.get(stack.pop(), []):
                if t not in keep:
                    keep.add(t)
                    stack.append(t)
        keep |= {s for s in in_edges.get(start, []) if by_id[s]["type"] == "repo_state"}
    edges = [e for e in graph["edges"] if e["source"] in keep and e["target"] in keep and e["relation"] in chain_rel
             and not (error_id and e["relation"] == "USES" and error_id not in e["chains"])]
    return {"collection": graph["collection"], "nodes": [by_id[i] for i in keep], "edges": edges}
