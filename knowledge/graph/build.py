"""Build the knowledge graph with provenance on every node and every edge.

Input: the provenance store (provenance.py: documents + chunks), the playbook, and the cached
LLM relations (llm_relations.py). Output: knowledge/graph/data/graph.json + merge_log.json.

Node:  id, name, type, aliases, description, collections, sources, mentions[], degree, community
       mention = {chunk_id, document_id, start, end, confidence, method}   (span in chunk text)
Edge:  id, source, target, relation, weight, confidence, method, evidence[], evidence_total
       evidence = {chunk_id, document_id, quote (<= 25 words, verbatim), section, page?}

Relations (directed):
  document -HAS_SECTION-> section                       structure
  section  -MENTIONS->    command|flag|env|code|symbol|setting
  error    -DOCUMENTED_IN-> section                     rule: the error line is printed there
  command  -HAS_OPTION->  flag                          rule: flag on the same command line
  command  -RISK->        risk_tier                     rule: mcp_servers/git_local/risk.py
  repo_state -LEADS_TO->  error                         playbook
  error    -CAUSED_BY->   cause                         playbook, LLM
  cause    -FIXED_BY->    command|manual_step           playbook, LLM
  command  -REQUIRES->    concept                       playbook, LLM
  command  -UNDONE_BY->   command                       LLM
  concept  -EXPLAINED_IN-> section                      playbook doc links, LLM
No evidence, no edge: every edge is created together with the chunk and quote it came from.
"""

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from knowledge.graph import canon as canon_mod
from knowledge.graph import rules
from knowledge.graph.provenance import DATA, load_chunks, load_documents
from knowledge.graph.troubleshoot import load as load_playbook
from knowledge.graph.troubleshoot import resolve_doc
from mcp_servers.git_local.risk import classify

ENTITY_TYPES = ("command", "flag", "env", "code", "symbol", "setting", "error", "concept", "cause",
                "manual_step", "repo_state", "risk_tier")
STRUCTURE_TYPES = ("document", "section")
RELATION_FAMILY = {"HAS_SECTION": "structure", "MENTIONS": "mention", "DOCUMENTED_IN": "docs",
                   "EXPLAINED_IN": "docs", "HAS_OPTION": "command", "RISK": "command", "REQUIRES": "command",
                   "LEADS_TO": "troubleshooting", "CAUSED_BY": "troubleshooting", "FIXED_BY": "troubleshooting",
                   "UNDONE_BY": "troubleshooting"}
MAX_EVIDENCE = 8          # evidence items kept per edge; evidence_total counts them all
TOOLS_SOURCES_FIRST = ("progit_book", "github_git_docs", "git_official_docs")   # descriptions prefer prose


def edge_id(src: str, rel: str, tgt: str) -> str:
    return "e" + hashlib.sha1(f"{src}|{rel}|{tgt}".encode()).hexdigest()[:12]


class Graph:
    def __init__(self, chunks_by_id: dict, docs_by_id: dict, canon: canon_mod.Canon):
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self.chunks = chunks_by_id
        self.docs = docs_by_id
        self.canon = canon

    # -- nodes --------------------------------------------------------------------------
    def node(self, typ: str, key: str, name: str | None = None, **extra) -> str:
        nid = f"{typ}:{key}"
        n = self.nodes.get(nid)
        if n is None:
            n = self.nodes[nid] = {"id": nid, "name": name or key, "type": typ, "aliases": set(),
                                   "description": "", "collections": set(), "sources": set(),
                                   "mentions": {}, **extra}
        else:
            for k, v in extra.items():
                n.setdefault(k, v)
        return nid

    def entity(self, typ: str, raw: str, name: str | None = None, **extra) -> str:
        typ, key = self.canon.resolve(typ, raw)
        nid = self.node(typ, key, name, **extra)
        if raw != key:
            self.nodes[nid]["aliases"].add(raw)
        return nid

    def mention(self, nid: str, chunk: dict, start: int, end: int, confidence: float, method: str) -> None:
        n = self.nodes[nid]
        if chunk["id"] not in n["mentions"]:
            n["mentions"][chunk["id"]] = {"chunk_id": chunk["id"], "document_id": chunk["document_id"],
                                          "start": start, "end": end, "confidence": confidence, "method": method}
        n["collections"].add(chunk["collection"])
        n["sources"].add(chunk["source"])

    # -- edges --------------------------------------------------------------------------
    def edge(self, src: str, rel: str, tgt: str, chunk: dict, quote: str, confidence: float, method: str,
             note: str | None = None, **attrs) -> dict | None:
        quote = quote.strip()
        if not quote or src == tgt:
            return None                                   # no evidence, no edge
        e = self.edges.get((src, rel, tgt))
        if e is None:
            e = self.edges[(src, rel, tgt)] = {
                "id": edge_id(src, rel, tgt), "source": src, "target": tgt, "relation": rel,
                "family": RELATION_FAMILY[rel], "weight": 0, "confidence": 0.0, "methods": set(),
                "evidence": [], "evidence_total": 0, **attrs}
        if any(ev["chunk_id"] == chunk["id"] and ev["quote"] == quote for ev in e["evidence"]):
            return e
        e["weight"] += 1
        e["evidence_total"] += 1
        e["confidence"] = max(e["confidence"], confidence)
        e["methods"].add(method)
        if len(e["evidence"]) < MAX_EVIDENCE:
            ev = {"chunk_id": chunk["id"], "document_id": chunk["document_id"], "quote": quote,
                  "section": chunk["section"], "method": method}
            if chunk.get("page_start"):
                ev["page"] = chunk["page_start"]
            if note:
                ev["note"] = note
            e["evidence"].append(ev)
        return e


# ---- passes ----------------------------------------------------------------------------

def section_id(chunk: dict) -> str:
    return f"section:{chunk['document_id']}#{chunk['section'] or '(top)'}"


def title_len(chunk: dict) -> int:
    """Length of the 'section title' prefix that every chunk of a section repeats."""
    i = chunk["text"].find("\n\n")
    return i + 2 if i >= 0 else 0


def structure(g: Graph) -> None:
    for d in g.docs.values():
        nid = g.node("document", d["id"], d["title"], document_id=d["id"], file_type=d["file_type"],
                     source=d["source"], path=d["path"], url=d["url"], license=d["license"],
                     attribution=d["attribution"])
        g.nodes[nid]["collections"].add(d["collection"])
        g.nodes[nid]["sources"].add(d["source"])
        if d.get("pages"):
            g.nodes[nid]["pages"] = d["pages"]
    for c in g.chunks.values():
        sec = c["section"] or "(top)"
        sid = g.node("section", f"{c['document_id']}#{sec}", sec.split(" > ")[-1], document_id=c["document_id"],
                     section=sec, path=g.docs[c["document_id"]]["path"])
        n = g.nodes[sid]
        tl = max(title_len(c) - 2, 0)
        g.mention(sid, c, 0, tl, 1.0, "structure")
        if c.get("page_start"):
            n["page_start"] = min(n.get("page_start", c["page_start"]), c["page_start"])
            n["page_end"] = max(n.get("page_end", c["page_end"]), c["page_end"])
        g.edge(f"document:{c['document_id']}", "HAS_SECTION", sid, c, rules.quote(c["text"], 0, tl) if tl else sec,
               1.0, "structure")
        if not n["description"]:
            body = c["text"][title_len(c):]
            n["description"] = rules.sentence(body, 0, 30) if body.strip() else ""


def playbook_aliases(pb: dict) -> dict[str, list[str]]:
    """playbook error id -> canonical keys of the error lines its message is made of."""
    out = {}
    for eid, e in pb["errors"].items():
        parts = re.split(r"\s{2,}|(?<=[.)])\s+(?=[A-Z!])", e["message"])
        keys = []
        for p in parts:
            m = rules.ERROR_LINE.match(p)
            k = rules.error_key(m.group(1) if m else p)
            if len(k.split()) >= 3 and k not in keys:
                keys.append(k)
        out[eid] = keys
    return out


def doc_mentions(g: Graph, error_folds: dict[str, str]) -> None:
    """Rule mentions of every non-playbook chunk -> MENTIONS / DOCUMENTED_IN edges."""
    from knowledge.graph.entities import extract
    known = set()
    for c in g.chunks.values():
        if c["collection"] != "playbook":
            known |= {k for k, kind in extract(c["text"]).items() if kind == "code"}
    for c in g.chunks.values():
        if c["collection"] == "playbook":
            continue
        sid = section_id(c)
        text = c["text"]
        for m in rules.mentions(text, known, skip_before=title_len(c)):
            raw = m.key
            if m.type == "error" and raw in error_folds:
                nid = f"error:{g.canon.alias('error', raw, error_folds[raw], 'error_line')}"
                if nid not in g.nodes:
                    g.node("error", nid.split(":", 1)[1])
                if raw != nid.split(":", 1)[1]:
                    g.nodes[nid]["aliases"].add(raw)
            else:
                nid = g.entity(m.type, raw, text[m.start:m.end] if m.type == "error" else None)
            g.mention(nid, c, m.start, m.end, m.confidence, "rule")
            q = rules.quote(text, m.start, m.end)
            if m.type == "error":
                g.edge(nid, "DOCUMENTED_IN", sid, c, q, m.confidence, "rule")
            else:
                g.edge(sid, "MENTIONS", nid, c, q, m.confidence, "rule")
        for cmd, line, flags, ls, le in rules.command_lines(text):
            if ls < title_len(c):
                continue
            cid = g.entity("command", cmd)
            pos = text.find(line, ls)
            q = rules.quote(text, pos if pos >= 0 else ls, (pos + len(line)) if pos >= 0 else le)
            for f in dict.fromkeys(flags):
                fid = g.entity("flag", f)
                g.edge(cid, "HAS_OPTION", fid, c, q, 0.9, "rule")
            if cmd.startswith("git "):
                r = classify(line)
                if r["key"] not in ("unknown", "outside"):
                    tid = g.node("risk_tier", r["tier"], r["tier"])
                    g.edge(cid, "RISK", tid, c, q, 0.9, "rule", note=f"risk.py: {r['reason']}")


def find_span(text: str, needle: str) -> tuple[int, int]:
    i = text.find(needle)
    return (i, i + len(needle)) if i >= 0 else (title_len_text(text), len(text))


def title_len_text(text: str) -> int:
    i = text.find("\n\n")
    return i + 2 if i >= 0 else 0


def playbook(g: Graph, pb: dict, error_keys: dict[str, list[str]]) -> list[str]:
    """Troubleshooting chains from the hand-written playbook, each edge quoting its entry."""
    pchunks = {c["key"]: c for c in g.chunks.values() if c["collection"] == "playbook"}
    unresolved = []
    if not pchunks:
        return unresolved

    def q(chunk, needle):
        s, e = find_span(chunk["text"], needle)
        return rules.quote(chunk["text"], s, e), s, e

    for sid, s in pb["repo_states"].items():
        c = pchunks[f"repo_states/{sid}"]
        nid = g.node("repo_state", sid, s["label"])
        g.nodes[nid]["description"] = s.get("detail", "")
        _, a, b = q(c, s["label"])
        g.mention(nid, c, a, b, 1.0, "playbook")
    sections = [{"id": n["id"], "path": n["path"], "section": n["section"]}
                for n in g.nodes.values() if n["type"] == "section" and "git_docs" in n["collections"]]
    documents = [{"id": n["id"], "path": n["path"]} for n in g.nodes.values()
                 if n["type"] == "document" and "git_docs" in n["collections"]]
    for cid, con in pb["concepts"].items():
        c = pchunks[f"concepts/{cid}"]
        nid = g.entity("concept", con["label"], con["label"])
        g.nodes[nid]["aliases"].add(cid)
        _, a, b = q(c, con["label"])
        g.mention(nid, c, a, b, 1.0, "playbook")
        for ref in con.get("docs", []):
            target = resolve_doc(ref, sections, documents)
            if target is None:
                unresolved.append(f"{cid}: {ref}")
                continue
            tgt = target["id"]
            quote, _, _ = q(c, ref)
            g.edge(nid, "EXPLAINED_IN", tgt, c, quote, 1.0, "playbook")
            # second evidence item: the section itself, quoted by its heading
            tc = next((ch for ch in g.chunks.values() if section_id(ch) == tgt
                       or f"document:{ch['document_id']}" == tgt), None)
            if tc is not None:
                g.edge(nid, "EXPLAINED_IN", tgt, tc, rules.quote(tc["text"], 0, max(title_len(tc) - 2, 1)),
                       1.0, "playbook")
    for eid, e in pb["errors"].items():
        c = pchunks[f"errors/{eid}"]
        key = error_keys[eid][0]
        err = g.node("error", key, e["message"].split("  ")[0][:90], playbook_id=eid)
        g.nodes[err]["description"] = e["message"]
        g.nodes[err]["aliases"].add(eid)
        _, a, b = q(c, e["message"][:40])
        g.mention(err, c, a, b, 1.0, "playbook")
        for s in e.get("states", []):
            quote, _, _ = q(c, "states:")
            g.edge(f"repo_state:{s}", "LEADS_TO", err, c, quote, 1.0, "playbook")
        for i, cause in enumerate(e["causes"]):
            cause_id = g.node("cause", f"{eid}/{i}", cause["text"])
            g.nodes[cause_id]["description"] = cause.get("note", "")
            quote, a, b = q(c, cause["text"])
            g.mention(cause_id, c, a, b, 1.0, "playbook")
            g.edge(err, "CAUSED_BY", cause_id, c, quote, 1.0, "playbook")
            for order, step in enumerate(cause["commands"], 1):
                quote, a, b = q(c, step["cmd"])
                if step.get("manual"):
                    kid = g.node("manual_step", step["cmd"].lower(), step["cmd"])
                else:
                    m = rules.COMMAND.match(step["cmd"])
                    key = rules.command_key(m.group(1), m.group(2)) if m else None
                    if key is None:
                        continue
                    kid = g.entity("command", key)
                    for f in dict.fromkeys(rules.FLAG.findall(step["cmd"])):
                        g.edge(kid, "HAS_OPTION", g.entity("flag", rules.norm(f)), c, quote, 1.0, "playbook")
                    r = classify(step["cmd"])
                    if r["key"] not in ("unknown", "outside"):
                        g.edge(kid, "RISK", g.node("risk_tier", r["tier"], r["tier"]), c, quote, 1.0, "playbook",
                               note=f"risk.py: {r['reason']}")
                g.mention(kid, c, a, b, 1.0, "playbook")
                g.edge(cause_id, "FIXED_BY", kid, c, quote, 1.0, "playbook", order=order)
                for con in step.get("concepts", []):
                    target = g.entity("concept", pb["concepts"][con]["label"])
                    g.edge(kid, "REQUIRES", target, c, quote, 1.0, "playbook")
    return unresolved


def llm_relations(g: Graph, folder: Path) -> int:
    """Typed relations the LLM extracted (already validated and cached by llm_relations.py)."""
    from knowledge.graph.llm_relations import load_cached
    n = 0
    for rec in load_cached(folder):
        c = g.chunks.get(rec["chunk_id"])
        if c is None:
            continue
        sid = section_id(c)
        for r in rec["relations"]:
            ids = []
            for side in ("subject", "object"):
                typ, name = r[f"{side}_type"], r[side]
                if typ == "section":
                    ids.append(sid)
                    continue
                nid = g.entity(typ, name.lower() if typ in ("command", "flag", "error") else name, name)
                s, e = find_span(c["text"], name)
                g.mention(nid, c, s, e, 0.7, "llm")
                ids.append(nid)
            if g.edge(ids[0], r["relation"], ids[1], c, r["quote"], 0.7, "llm", note=rec.get("model")):
                n += 1
    return n


def describe(g: Graph) -> None:
    """Short descriptions: a command's NAME line from its man page, otherwise the sentence of
    its first mention (Git sources first)."""
    for c in g.chunks.values():
        if c["source"] == "git_official_docs" and c["section"].endswith("> NAME"):
            m = re.search(r"git-([a-z-]+) - (.+)", c["text"])
            if m and f"command:git {m.group(1)}" in g.nodes:
                g.nodes[f"command:git {m.group(1)}"]["description"] = m.group(2).strip()
    rank = {s: i for i, s in enumerate(TOOLS_SOURCES_FIRST)}
    for n in g.nodes.values():
        if n["description"] or not n["mentions"]:
            continue
        ms = sorted(n["mentions"].values(), key=lambda m: (rank.get(g.chunks[m["chunk_id"]]["source"], 9),
                                                           m["chunk_id"]))
        # first prose sentence (not a shell line, not a code block) of at least 6 words
        for m in ms[:40]:
            sent = rules.sentence(g.chunks[m["chunk_id"]]["text"], m["start"], 30)
            if len(sent.split()) >= 6 and not sent.lstrip().startswith(("$", "```", "-", "|", "+")) and "```" not in sent:
                n["description"] = sent
                break
        if not n["description"]:            # no prose around it: the line it was found in
            m = ms[0]
            n["description"] = rules.quote(g.chunks[m["chunk_id"]]["text"], m["start"], m["end"])


def communities(nodes: list[dict], edges: list[dict], seed: int = 7) -> list[dict]:
    """Louvain (networkx, seeded). Risk tiers and documents are left out of the clustering:
    they are hubs that would glue every topic together. They join their neighbours' majority."""
    import networkx as nx
    from networkx.algorithms.community import louvain_communities
    skip = {"risk_tier", "document"}
    typ = {n["id"]: n["type"] for n in nodes}
    G = nx.Graph()
    for e in edges:
        a, b = e["source"], e["target"]
        if typ[a] in skip or typ[b] in skip:
            continue
        w = G[a][b]["weight"] + 1 if G.has_edge(a, b) else 1
        G.add_edge(a, b, weight=w)
    parts = louvain_communities(G, weight="weight", seed=seed, resolution=1.0)
    parts = sorted(parts, key=lambda p: (-len(p), min(p)))
    member = {nid: i for i, p in enumerate(parts) for nid in p}
    # Nodes left out (documents, risk tiers, sections that mention nothing) join the community
    # most of their neighbours are in; two rounds so a section can follow its document.
    nbrs = defaultdict(list)
    for e in edges:
        nbrs[e["source"]].append(e["target"])
        nbrs[e["target"]].append(e["source"])
    for _ in range(2):
        for nid in sorted(typ):
            if nid not in member:
                votes = Counter(member[x] for x in nbrs[nid] if x in member)
                if votes:
                    member[nid] = min(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return [{"id": i, "members": sorted(n for n, c in member.items() if c == i)} for i in range(len(parts))]


def finish(g: Graph, with_communities: bool = True, labels: dict | None = None) -> dict:
    nodes = sorted(g.nodes.values(), key=lambda n: n["id"])
    edges = sorted(g.edges.values(), key=lambda e: e["id"])
    nbrs = defaultdict(set)
    for e in edges:
        nbrs[e["source"]].add(e["target"])
        nbrs[e["target"]].add(e["source"])
    for n in nodes:
        n["aliases"] = sorted(n["aliases"] - {n["name"]})
        n["collections"] = sorted(n["collections"])
        n["sources"] = sorted(n["sources"])
        n["mentions"] = sorted(n["mentions"].values(), key=lambda m: (m["document_id"], m["chunk_id"]))
        n["degree"] = len(nbrs[n["id"]])
        n["community"] = None
    for e in edges:
        e["methods"] = sorted(e["methods"])
        e["method"] = e["methods"][0] if len(e["methods"]) == 1 else "+".join(e["methods"])
        e["confidence"] = round(e["confidence"], 2)
    comms = []
    if with_communities:
        by_id = {n["id"]: n for n in nodes}
        for c in communities(nodes, edges):
            for nid in c["members"]:
                by_id[nid]["community"] = c["id"]
            members = [by_id[m] for m in c["members"]]
            top = sorted((m for m in members if m["type"] not in ("document", "section", "risk_tier")),
                         key=lambda m: (-m["degree"], m["id"]))[:5]
            docs = Counter(m["sources"][0] for m in members if m["sources"])
            rule_label = " / ".join(m["name"] for m in top[:3]) or (members[0]["name"] if members else "")
            comms.append({"id": c["id"], "size": len(members), "label": (labels or {}).get(str(c["id"]), rule_label),
                          "rule_label": rule_label, "top": [m["id"] for m in top],
                          "sources": dict(docs.most_common(4)),
                          "types": dict(Counter(m["type"] for m in members).most_common())})
    return {"nodes": nodes, "edges": edges, "communities": comms}


def build(data: Path = DATA, with_llm: bool = True, with_communities: bool = True,
          decisions: dict | None = None, write: bool = True, playbook_path: Path | None = None) -> dict:
    docs = {d["id"]: d for d in load_documents(data)}
    chunks = {c["id"]: c for c in load_chunks(data)}
    canon = canon_mod.Canon(decisions if decisions is not None else canon_mod.load_decisions())
    g = Graph(chunks, docs, canon)
    structure(g)
    pb = load_playbook(playbook_path) if playbook_path else load_playbook()
    error_keys = playbook_aliases(pb)
    # A doc error equal to (or starting with) exactly one line of a playbook message is that error.
    folds: dict[str, str] = {}
    all_doc_errors = {m.key for c in chunks.values() if c["collection"] != "playbook"
                      for m in rules.mentions(c["text"], None, title_len(c)) if m.type == "error"}
    for raw in all_doc_errors:
        owners = {keys[0] for keys in error_keys.values()
                  for k in keys if raw == k or (len(k.split()) >= 4 and raw.startswith(k))}
        if len(owners) == 1:
            folds[raw] = owners.pop()
    doc_mentions(g, folds)
    unresolved = playbook(g, pb, error_keys)
    n_llm = llm_relations(g, data / "llm_cache") if with_llm else 0
    describe(g)
    labels = {}
    lab = data / "community_labels.json"
    if lab.exists():
        labels = json.loads(lab.read_text(encoding="utf-8"))
    out = finish(g, with_communities, labels)
    out["meta"] = {
        "collections": sorted({d["collection"] for d in docs.values()}),
        "sources": {d["source"]: {"license": d["license"], "attribution": d["attribution"]} for d in docs.values()},
        "llm_edges": n_llm, "unresolved_playbook_links": unresolved,
        "counts": {"nodes": dict(Counter(n["type"] for n in out["nodes"])),
                   "edges": dict(Counter(e["relation"] for e in out["edges"]))},
    }
    merge_log = sorted(canon.log.values(), key=lambda m: m["id"])
    if write:
        (data / "graph.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (data / "merge_log.json").write_text(json.dumps(merge_log, ensure_ascii=False, indent=1), encoding="utf-8")
    out["merge_log"] = merge_log
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="build knowledge/graph/data/graph.json from the provenance store")
    ap.add_argument("--no-llm", action="store_true", help="leave the cached LLM relations out")
    ap.add_argument("--no-communities", action="store_true")
    a = ap.parse_args()
    g = build(with_llm=not a.no_llm, with_communities=not a.no_communities)
    print(f"{len(g['nodes'])} nodes {g['meta']['counts']['nodes']}")
    print(f"{len(g['edges'])} edges {g['meta']['counts']['edges']}")
    print(f"{len(g['communities'])} communities, {len(g['merge_log'])} merges logged, {g['meta']['llm_edges']} LLM edges")
    for u in g["meta"]["unresolved_playbook_links"]:
        print(f"  playbook doc link not found in the corpus: {u}")


if __name__ == "__main__":
    main()
