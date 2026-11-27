"""Graph-aware retrieval: question entities -> the chunks that mention them -> one ranked list,
fused with the dense (and BM25) lists by Reciprocal Rank Fusion.

  1. Find the question's entities: the same rule extractor as the graph (commands, flags, env
     vars, error lines, code names) plus the names and aliases of concept, code and symbol
     nodes matched as 1-4 word phrases.
  2. Every mention of a matched entity votes for its chunk with weight 1 / log2(2 + mentions),
     so a rare, specific name (--maxfail) counts more than a common one (git commit).
  3. Expansion: one hop along typed relations (CAUSED_BY, FIXED_BY, HAS_OPTION, REQUIRES,
     UNDONE_BY, DOCUMENTED_IN, EXPLAINED_IN) adds the neighbours' chunks at half weight. This is
     the multi-hop part: an error leads to its fix command, a concept to its section.
  4. A chunk that holds several question entities collects several votes and rises.

Only chunks of the searched collection are returned (never the playbook). The graph is read
from knowledge/graph/data/graph.json once per process.
"""

import math
import re
import time
from collections import defaultdict
from functools import lru_cache

from knowledge.graph import rules
from knowledge.graph.canon import Canon
from knowledge.graph.provenance import DATA, load_chunks, load_documents

EXPAND = {"CAUSED_BY", "FIXED_BY", "HAS_OPTION", "REQUIRES", "UNDONE_BY", "DOCUMENTED_IN", "EXPLAINED_IN"}
PHRASE_TYPES = {"concept", "code", "symbol", "command", "error", "flag", "env"}
MIN_PHRASE = 4            # characters: shorter names match too much ("run", "app")
CANDIDATES = 20
STOP_PHRASES = {"what", "when", "which", "where", "does", "how", "the", "and", "for", "with", "that",
                "this", "from", "into", "your", "you", "can", "should", "default", "value", "file",
                "files", "use", "used", "using", "set", "run", "job", "jobs", "test", "tests", "name",
                "make", "after", "before", "without", "between", "both", "each", "there", "their"}


class GraphIndex:
    def __init__(self, graph: dict, chunks: dict):
        self.chunks = chunks
        self.nodes = {n["id"]: n for n in graph["nodes"]}
        self.names: dict[str, set[str]] = defaultdict(set)
        for n in graph["nodes"]:
            if n["type"] not in PHRASE_TYPES:
                continue
            for name in [n["name"], *n.get("aliases", [])]:
                k = " ".join(str(name).lower().split())
                if self.specific(k, n):
                    self.names[k].add(n["id"])
        self.out: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for e in graph["edges"]:
            if e["relation"] in EXPAND:
                self.out[e["source"]].append((e["relation"], e["target"]))
                self.out[e["target"]].append((e["relation"], e["source"]))
        self.canon = Canon()

    @staticmethod
    def specific(name: str, node: dict) -> bool:
        """Can this name be matched as a plain phrase in a question? Only when it cannot be an
        ordinary English word: it has a non-letter (timeout-minutes, conftest.py), or it is a
        concept/command/error name, or it is a rare single word (few mentions). A plain word
        like 'step' or 'line' appears in every question and every chunk."""
        words = name.split()
        if len(name) < MIN_PHRASE or name in STOP_PHRASES or not words:
            return False
        if words[0] in STOP_PHRASES or words[-1] in STOP_PHRASES:
            return False
        if node["type"] in ("concept", "command", "error"):
            return True
        if re.search(r"[^a-z ]", name):
            return True
        return len(words) == 1 and len(node["mentions"]) <= 15

    def entities(self, question: str) -> dict[str, str]:
        """node id -> how it was found ('rule' or 'phrase')."""
        found = {}
        for m in rules.mentions(question):
            typ, key = self.canon.resolve(m.type, m.key)
            nid = f"{typ}:{key}"
            if nid in self.nodes:
                found[nid] = "rule"
        words = re.findall(r"[\w./<>-]+", question.lower())
        for n in range(4, 0, -1):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i:i + n]).strip(".,?")
                for nid in self.names.get(phrase, ()):
                    found.setdefault(nid, "phrase")
        return found

    def chunk_scores(self, question: str, collection: str) -> tuple[dict[str, float], dict]:
        found = self.entities(question)
        scores: dict[str, float] = defaultdict(float)
        trace = {"entities": found, "expanded": {}}

        def vote(nid: str, weight: float):
            n = self.nodes.get(nid)
            if n is None:
                return
            ms = [m for m in n["mentions"] if self.chunks.get(m["chunk_id"], {}).get("collection") == collection]
            if not ms:
                return
            w = weight / math.log2(2 + len(ms))
            for m in ms:
                scores[m["chunk_id"]] += w

        for nid in found:
            vote(nid, 1.0)
            for rel, other in self.out.get(nid, []):
                if other not in found:
                    trace["expanded"].setdefault(other, (rel, nid))
                    if other.startswith("section:"):
                        # a section reached by DOCUMENTED_IN / EXPLAINED_IN: its own chunks
                        for m in self.nodes[other]["mentions"]:
                            if self.chunks.get(m["chunk_id"], {}).get("collection") == collection:
                                scores[m["chunk_id"]] += 0.5
                    else:
                        vote(other, 0.5)
        return scores, trace


@lru_cache
def index() -> GraphIndex:
    import json
    t0 = time.perf_counter()
    g = json.loads((DATA / "graph.json").read_text(encoding="utf-8"))
    chunks = {c["id"]: c for c in load_chunks()}
    ix = GraphIndex(g, chunks)
    ix.urls = {d["id"]: d["url"] for d in load_documents()}
    ix.load_seconds = time.perf_counter() - t0
    return ix


def graph_search(question: str, k: int, collection: str, ix: GraphIndex | None = None):
    """Top-k chunks by graph votes, as retrieve.Hit objects."""
    from knowledge.retrieve import Hit
    ix = ix or index()
    scores, _ = ix.chunk_scores(question, collection)
    best = sorted(scores, key=lambda c: (-scores[c], c))[:k]
    out = []
    for cid in best:
        c = ix.chunks[cid]
        doc = c["document_id"].split(":", 1)
        out.append(Hit(chunk_id=cid, score=scores[cid], text=c["text"], source=c["source"],
                       path=doc[1] if len(doc) > 1 else c["document_id"], section=c["section"],
                       url=getattr(ix, "urls", {}).get(c["document_id"])))
    return out


def explain(question: str, collection: str) -> dict:
    """What the graph saw in a question: matched entities and the hop that added a neighbour
    (used by the viewer's answer-trace mode)."""
    ix = index()
    scores, trace = ix.chunk_scores(question, collection)
    top = sorted(scores, key=lambda c: (-scores[c], c))[:CANDIDATES]
    return {"entities": [{"id": n, "name": ix.nodes[n]["name"], "type": ix.nodes[n]["type"], "found_by": how}
                         for n, how in trace["entities"].items()],
            "expanded": [{"id": o, "via": rel, "from": src} for o, (rel, src) in list(trace["expanded"].items())[:50]],
            "chunks": [{"chunk_id": c, "score": round(scores[c], 3)} for c in top]}
