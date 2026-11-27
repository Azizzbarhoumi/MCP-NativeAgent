"""Read side of the graph: what the API and the viewer ask for. No writes, no network.

Loaded once per process from knowledge/graph/data/ (graph.json, documents.json, chunks.jsonl)
and from eval/results/knowledge_*.json (which chunks each benchmark answer used).
"""

import json
import re
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path

from knowledge.graph.provenance import DATA, load_chunks, load_documents
from knowledge.sources.local_docs import CORPUS

RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"
HUBS = {"risk_tier"}             # never walked through in a path: every command touches them
EXCERPT = 600


class Store:
    def __init__(self, data: Path = DATA, results: Path = RESULTS):
        g = json.loads((data / "graph.json").read_text(encoding="utf-8"))
        self.nodes = {n["id"]: n for n in g["nodes"]}
        self.edges = {e["id"]: e for e in g["edges"]}
        self.communities = {c["id"]: c for c in g.get("communities", [])}
        self.meta = g.get("meta", {})
        self.docs = {d["id"]: d for d in load_documents(data)}
        self.chunks = {c["id"]: c for c in load_chunks(data)}
        self.by_doc = defaultdict(list)
        for c in self.chunks.values():
            self.by_doc[c["document_id"]].append(c)
        self.adj: dict[str, list[str]] = defaultdict(list)
        for e in g["edges"]:
            self.adj[e["source"]].append(e["id"])
            self.adj[e["target"]].append(e["id"])
        self.answers = load_answers(results)
        self.used = defaultdict(list)            # chunk id -> [(run, question id)]
        for run, rows in self.answers.items():
            for r in rows:
                for rank, h in enumerate(r.get("retrieved", []), 1):
                    self.used[h["chunk_id"]].append({"run": run, "id": r["id"], "rank": rank,
                                                     "verdict": r.get("verdict"), "question": r["question"]})

    # ---- summaries ---------------------------------------------------------------------
    def summary(self, n: dict) -> dict:
        s = {k: n.get(k) for k in ("id", "name", "type", "degree", "community", "collections", "sources")}
        s["has_pdf"] = any(self.docs.get(m["document_id"], {}).get("file_type") == "pdf" for m in n["mentions"][:200]) \
            or n.get("file_type") == "pdf"
        s["mentions"] = len(n["mentions"])
        return s

    @staticmethod
    def edge_summary(e: dict) -> dict:
        return {k: e[k] for k in ("id", "source", "target", "relation", "family", "weight", "confidence", "method")}

    # ---- graph view ----------------------------------------------------------------------
    def select(self, types=None, relations=None, collections=None, communities=None, min_confidence=0.0,
               has_pdf=False, focus=None, depth=1, max_nodes=1500, view="entities", q=None) -> dict:
        def node_ok(n):
            return ((not types or n["type"] in types) and
                    (not collections or set(n["collections"]) & set(collections)) and
                    (communities is None or n["community"] in communities) and
                    (not has_pdf or self.summary(n)["has_pdf"]))

        def edge_ok(e):
            return (not relations or e["relation"] in relations) and e["confidence"] >= min_confidence

        if view == "communities" and not focus:
            return self.community_view(collections)
        if focus:
            if focus not in self.nodes:
                raise KeyError(f"unknown node {focus!r}")
            keep, frontier = {focus}, {focus}
            for _ in range(max(depth, 0)):
                nxt = set()
                for f in frontier:
                    for eid in self.adj[f]:
                        e = self.edges[eid]
                        if not edge_ok(e):
                            continue
                        other = e["target"] if e["source"] == f else e["source"]
                        if other not in keep and (other == focus or node_ok(self.nodes[other])):
                            nxt.add(other)
                for o in sorted(nxt, key=lambda x: -self.nodes[x]["degree"]):
                    if len(keep) >= max_nodes:
                        break
                    keep.add(o)
                frontier = nxt & keep
        else:
            cands = [n for n in self.nodes.values() if node_ok(n)]
            if view == "documents":
                cands = [n for n in cands if n["type"] in ("document",) or (n["type"] not in ("section",)
                                                                           and n["degree"] >= 3)]
            elif not types:
                cands = [n for n in cands if n["type"] != "section"]
            # leaves (one mention, degree 1) only when nothing else fills the budget
            cands.sort(key=lambda n: (-(n["type"] == "document" and view == "documents"), -n["degree"], n["id"]))
            keep = {n["id"] for n in cands[:max_nodes]}
        edges = [e for e in self.edges.values() if e["source"] in keep and e["target"] in keep and edge_ok(e)]
        if view == "documents" and not focus:
            edges += self.document_edges(keep)
        matching = sum(1 for n in self.nodes.values() if node_ok(n))
        return {"nodes": [self.summary(self.nodes[i]) for i in sorted(keep)],
                "edges": [self.edge_summary(e) for e in edges],
                "total_nodes": len(self.nodes), "matching_nodes": matching,
                "hidden": max(matching - len(keep), 0) if not focus else None}

    def document_edges(self, keep: set) -> list[dict]:
        """Documents view: a document is joined to an entity its sections mention (aggregated,
        evidence stays on the section edges, the inspector shows them)."""
        out = {}
        for e in self.edges.values():
            if e["relation"] not in ("MENTIONS", "DOCUMENTED_IN"):
                continue
            sec, ent = (e["source"], e["target"]) if e["relation"] == "MENTIONS" else (e["target"], e["source"])
            doc = "document:" + self.nodes[sec].get("document_id", "")
            if doc in keep and ent in keep:
                key = (doc, ent)
                agg = out.setdefault(key, {"id": f"agg:{doc}|{ent}", "source": doc, "target": ent,
                                           "relation": "MENTIONS", "family": "mention", "weight": 0,
                                           "confidence": e["confidence"], "method": "aggregate"})
                agg["weight"] += e["weight"]
        return list(out.values())

    def community_view(self, collections=None, min_size: int = 8) -> dict:
        """One node per community (size = members), joined by the number of edges between them.
        Communities under min_size members are counted as hidden (reach them by search/focus)."""
        nodes = []
        for c in self.communities.values():
            if c["size"] < min_size:
                continue
            members = c.get("members_cached") or [n for n in self.nodes.values() if n["community"] == c["id"]]
            c["members_cached"] = members
            colls = sorted({x for m in members for x in m["collections"]})
            if collections and not set(colls) & set(collections):
                continue
            per = defaultdict(int)
            for m in members:
                for x in m["collections"]:
                    per[x] += 1
            nodes.append({"id": f"community:{c['id']}", "name": c["label"], "type": "community", "degree": c["size"],
                          "group": max(per, key=per.get) if per else None,
                          "community": c["id"], "collections": colls, "sources": list(c["sources"]),
                          "size": c["size"], "top": c["top"], "has_pdf": "pdf_docs" in colls, "mentions": 0})
        ids = {n["community"] for n in nodes}
        between = defaultdict(int)
        for e in self.edges.values():
            a, b = self.nodes[e["source"]]["community"], self.nodes[e["target"]]["community"]
            if a != b and a in ids and b in ids:
                between[tuple(sorted((a, b)))] += 1
        edges = [{"id": f"cc:{a}-{b}", "source": f"community:{a}", "target": f"community:{b}", "relation": "LINKS",
                  "family": "community", "weight": w, "confidence": 1.0, "method": "aggregate"}
                 for (a, b), w in between.items() if w >= 2]
        return {"nodes": nodes, "edges": edges, "total_nodes": len(self.nodes), "matching_nodes": len(nodes),
                "hidden": sum(1 for c in self.communities.values() if c["size"] < min_size),
                "hidden_what": f"communities under {min_size} members"}

    # ---- inspectors --------------------------------------------------------------------
    def document_info(self, doc_id: str, pages: list[int] | None = None) -> dict:
        d = self.docs[doc_id]
        info = {k: d.get(k) for k in ("id", "title", "source", "path", "url", "license", "attribution",
                                      "collection", "file_type", "pages", "ingested_at", "content_hash")}
        info["can_open"] = bool(d.get("file"))
        info["page_links"] = sorted(set(pages or []))[:20]
        return info

    def chunk_view(self, chunk_id: str, start: int | None = None, end: int | None = None) -> dict:
        c = self.chunks[chunk_id]
        text = c["text"]
        if start is not None and end is not None and len(text) > EXCERPT:
            a = max(0, start - EXCERPT // 2)
            b = min(len(text), a + EXCERPT)
            excerpt, offset = text[a:b], a
        else:
            excerpt, offset = text[:EXCERPT * 2], 0
        out = {"chunk_id": chunk_id, "document_id": c["document_id"], "section": c["section"],
               "order": c["order"], "excerpt": excerpt, "excerpt_offset": offset, "length": len(text),
               "page_start": c.get("page_start"), "page_end": c.get("page_end"),
               "document_title": self.docs[c["document_id"]]["title"]}
        if start is not None:
            out["highlight"] = [start - offset, end - offset]
        return out

    def node(self, nid: str, chunk_limit: int = 40) -> dict:
        n = self.nodes[nid]
        by_doc = defaultdict(list)
        for m in n["mentions"]:
            by_doc[m["document_id"]].append(m)
        sources = []
        for doc_id, ms in sorted(by_doc.items(), key=lambda kv: -len(kv[1])):
            pages = [self.chunks[m["chunk_id"]].get("page_start") for m in ms]
            info = self.document_info(doc_id, [p for p in pages if p])
            info["mentions"] = len(ms)
            sources.append(info)
        if n["type"] == "document":
            sources = [self.document_info(n["document_id"])]
        chunks = [dict(self.chunk_view(m["chunk_id"], m["start"], m["end"]), confidence=m["confidence"],
                       method=m["method"]) for m in n["mentions"][:chunk_limit]]
        rel = defaultdict(list)
        for eid in self.adj[nid]:
            e = self.edges[eid]
            out = e["source"] == nid
            other = self.nodes[e["target"] if out else e["source"]]
            rel[e["relation"] + (" ->" if out else " <-")].append({
                "edge": e["id"], "other": other["id"], "other_name": other["name"], "other_type": other["type"],
                "confidence": e["confidence"], "method": e["method"], "weight": e["weight"],
                "quote": e["evidence"][0]["quote"] if e["evidence"] else ""})
        for v in rel.values():
            v.sort(key=lambda r: (-r["weight"], r["other_name"]))
        used = []
        seen = set()
        for m in n["mentions"]:
            for u in self.used.get(m["chunk_id"], []):
                key = (u["run"], u["id"])
                if key not in seen:
                    seen.add(key)
                    used.append(u)
        comm = self.communities.get(n.get("community"))
        return {**{k: v for k, v in n.items() if k != "mentions"}, "mention_count": len(n["mentions"]),
                "community_label": comm["label"] if comm else None,
                "sources": sources, "chunks": chunks, "relations": dict(sorted(rel.items())),
                "used_in_answers": used[:50]}

    def edge(self, eid: str) -> dict:
        e = self.edges[eid]
        ev = []
        for item in e["evidence"]:
            c = self.chunks[item["chunk_id"]]
            q = item["quote"]
            i = c["text"].find(q)
            ev.append({**item, "document": self.document_info(item["document_id"],
                                                              [c["page_start"]] if c.get("page_start") else []),
                       "chunk": self.chunk_view(item["chunk_id"], i if i >= 0 else None,
                                                (i + len(q)) if i >= 0 else None)})
        return {**e, "source_node": self.summary(self.nodes[e["source"]]),
                "target_node": self.summary(self.nodes[e["target"]]), "evidence": ev}

    def path(self, a: str, b: str, max_len: int = 6) -> dict:
        """Shortest path (fewest edges), never through risk tiers. Each step lists its evidence."""
        for x in (a, b):
            if x not in self.nodes:
                raise KeyError(f"unknown node {x!r}")
        prev = {a: None}
        dq = deque([a])
        while dq:
            cur = dq.popleft()
            if cur == b:
                break
            if len(self._trail(prev, cur)) > max_len:
                continue
            for eid in self.adj[cur]:
                e = self.edges[eid]
                o = e["target"] if e["source"] == cur else e["source"]
                if o not in prev and (self.nodes[o]["type"] not in HUBS or o == b):
                    prev[o] = (cur, eid)
                    dq.append(o)
        if b not in prev:
            return {"found": False, "nodes": [], "edges": []}
        trail = self._trail(prev, b)
        nodes, cur = [b], b
        while prev[cur]:
            cur = prev[cur][0]
            nodes.append(cur)
        nodes.reverse()
        return {"found": True, "nodes": [self.summary(self.nodes[i]) for i in nodes],
                "edges": [{**self.edge_summary(self.edges[eid]), "evidence": self.edges[eid]["evidence"][:2]}
                          for eid in trail]}

    @staticmethod
    def _trail(prev, node) -> list[str]:
        out = []
        while prev.get(node):
            node, eid = prev[node]
            out.append(eid)
        return list(reversed(out))

    def community(self, cid: int, limit: int = 300) -> dict:
        c = self.communities[cid]
        members = sorted((n for n in self.nodes.values() if n["community"] == cid), key=lambda n: -n["degree"])
        keep = {n["id"] for n in members[:limit]}
        edges = [self.edge_summary(e) for e in self.edges.values() if e["source"] in keep and e["target"] in keep]
        return {**{k: v for k, v in c.items() if k != "members_cached"},
                "nodes": [self.summary(n) for n in members[:limit]], "edges": edges,
                "hidden": max(len(members) - limit, 0)}

    def search(self, q: str, limit: int = 15) -> list[dict]:
        q = q.strip().lower()
        if not q:
            return []
        hits = []
        for n in self.nodes.values():
            names = [n["name"].lower(), *[a.lower() for a in n.get("aliases", [])]]
            best = None
            for name in names:
                if name == q:
                    best = 0
                elif name.startswith(q):
                    best = 1 if best is None else min(best, 1)
                elif q in name:
                    best = 2 if best is None else min(best, 2)
            if best is not None:
                hits.append((best, -n["degree"], n["id"]))
        hits.sort()
        return [{**self.summary(self.nodes[i]), "match": rank} for rank, _, i in hits[:limit]]

    def document_chunks(self, doc_id: str) -> dict:
        if doc_id not in self.docs:
            raise KeyError(doc_id)
        return {"document": self.document_info(doc_id),
                "chunks": [{"chunk_id": c["id"], "order": c["order"], "section": c["section"],
                            "page_start": c.get("page_start"), "page_end": c.get("page_end"),
                            "excerpt": c["text"][:EXCERPT], "length": len(c["text"])}
                           for c in sorted(self.by_doc[doc_id], key=lambda c: c["order"])]}

    def file_for(self, doc_id: str, corpus: Path | None = None) -> Path:
        """The file behind a document, only if it lies inside the corpus folder. The document id
        is looked up in the store first, so a request can never name an arbitrary path."""
        d = self.docs.get(doc_id)
        if d is None or not d.get("file"):
            raise KeyError(doc_id)
        root = (corpus or CORPUS).resolve()
        p = (root / d["file"]).resolve()
        if not p.is_relative_to(root) or not p.is_file():
            raise PermissionError(doc_id)
        return p

    # ---- answer trace ------------------------------------------------------------------
    def answer_runs(self) -> list[dict]:
        return [{"run": run, "questions": len(rows)} for run, rows in sorted(self.answers.items())]

    def trace(self, run: str, qid: str) -> dict:
        """How an answer was found: question entities -> retrieved chunks -> their sections,
        documents and the entities in them; cited chunks marked."""
        rows = {r["id"]: r for r in self.answers.get(run, [])}
        if qid not in rows:
            raise KeyError(f"{run}/{qid}")
        r = rows[qid]
        cited = set(r.get("cited") or [])
        steps, nodes, edges = [], set(), []
        for rank, h in enumerate(r.get("retrieved", []), 1):
            c = self.chunks.get(h["chunk_id"])
            item = {"rank": rank, "chunk_id": h["chunk_id"], "doc": h.get("doc"), "section": h.get("section"),
                    "score": h.get("score"), "cited": rank in cited, "in_graph": c is not None}
            if c is not None:
                sid = f"section:{c['document_id']}#{c['section'] or '(top)'}"
                ents = [n["id"] for n in (self.nodes[e["target"]] for eid in self.adj.get(sid, [])
                                          for e in [self.edges[eid]] if e["source"] == sid and e["relation"] == "MENTIONS")
                        if any(m["chunk_id"] == h["chunk_id"] for m in n["mentions"])]
                item |= {"section_node": sid, "document_node": f"document:{c['document_id']}",
                         "entities": ents[:15], "excerpt": c["text"][:300]}
                nodes |= {sid, f"document:{c['document_id']}", *ents[:15]}
            steps.append(item)
        q_ents = []
        try:
            from knowledge.graph.retrieval import GraphIndex
            ix = getattr(self, "_ix", None) or GraphIndex({"nodes": list(self.nodes.values()), "edges": []}, self.chunks)
            self._ix = ix
            q_ents = [i for i in ix.entities(r["question"])]
        except Exception:
            q_ents = []
        nodes |= set(q_ents)
        for nid in nodes:
            for eid in self.adj.get(nid, []):
                e = self.edges[eid]
                if e["source"] in nodes and e["target"] in nodes:
                    edges.append(self.edge_summary(e))
        return {"run": run, "id": qid, "question": r["question"], "answer": r.get("answer"),
                "verdict": r.get("verdict"), "expected_docs": r.get("expected_docs"),
                "question_entities": q_ents, "steps": steps,
                "nodes": [self.summary(self.nodes[i]) for i in sorted(nodes) if i in self.nodes],
                "edges": list({e["id"]: e for e in edges}.values())}


def load_answers(results: Path) -> dict[str, list[dict]]:
    """Final knowledge-eval result files (no partials, no re-judged copies)."""
    out = {}
    if not results.exists():
        return out
    for p in sorted(results.glob("knowledge_*.json")):
        if re.search(r"judge-|partial", p.name):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rows = [r for r in d.get("rows", []) if r.get("retrieved")]
        if rows:
            out[p.stem] = rows
    return out


@lru_cache
def store() -> Store:
    return Store()
