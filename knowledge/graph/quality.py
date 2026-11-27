"""Graph quality numbers, before and after, plus a sample of edges for a person to judge.

  python -m knowledge.graph.quality                 prints and writes eval/graph_quality.md
  python -m knowledge.graph.quality --review 40     also writes eval/graph_edge_review.md
  python -m knowledge.graph.quality --precision     reads your marks back from the review file

"Before" is the previous graph (knowledge/graph/export.py: rule entities, no canonical names,
no typed relations), rebuilt from the same corpus. The review file holds quotes from the docs,
so it is git-ignored; eval/graph_quality.md holds numbers only and is committed.
"""

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

from knowledge.graph.canon import duplicate_rate
from knowledge.graph.provenance import DATA, load_chunks, load_documents

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "eval" / "graph_quality.md"
REVIEW = ROOT / "eval" / "graph_edge_review.md"
ENTITY = {"command", "flag", "env", "code", "symbol", "setting", "error", "concept", "cause", "manual_step",
          "repo_state"}
SEED = 7


def load_graph(data: Path = DATA) -> dict:
    return json.loads((data / "graph.json").read_text(encoding="utf-8"))


def entity_sources(g: dict) -> dict[str, set]:
    return {n["id"]: set(n["sources"]) for n in g["nodes"]}


def is_cross_source(e: dict, src: dict[str, set]) -> bool:
    """The edge joins material from two different sources: some source of one end differs
    from some source of the other (a section of Pro Git mentioning a flag also in git docs)."""
    a, b = src.get(e["source"], set()), src.get(e["target"], set())
    return bool(a and b) and (len(a | b) > 1)


def metrics(g: dict, chunk_ids: set | None = None, doc_ids: set | None = None) -> dict:
    nodes, edges = g["nodes"], g["edges"]
    ents = [n for n in nodes if n["type"] in ENTITY]
    src = entity_sources(g)
    with_ev = sum(1 for e in edges if e["evidence"])
    ev_ok = None
    if chunk_ids is not None:
        items = [ev for e in edges for ev in e["evidence"]]
        ev_ok = sum(ev["chunk_id"] in chunk_ids and (doc_ids is None or ev["document_id"] in doc_ids)
                    for ev in items) / max(len(items), 1)
    names = [n["name"].lower() for n in ents]
    rnd = random.Random(SEED)
    sample = rnd.sample(names, min(300, len(names)))
    dup, pairs = duplicate_rate(names, sample)
    typed_names = [f"{n['type']}|{n['name'].lower()}" for n in ents]
    dup_t, _ = duplicate_rate(typed_names, random.Random(SEED).sample(typed_names, min(300, len(typed_names))))
    typed = [e for e in edges if e["family"] not in ("structure", "mention")]
    return {
        "nodes": len(nodes), "edges": len(edges), "entities": len(ents),
        "orphan_ratio": sum(n["degree"] == 0 for n in nodes) / max(len(nodes), 1),
        "avg_degree": 2 * len(edges) / max(len(nodes), 1),
        "edges_with_evidence": with_ev / max(len(edges), 1),
        "evidence_chunks_exist": ev_ok,
        "duplicate_rate": dup, "duplicate_examples": pairs[:12], "duplicate_rate_same_type": dup_t,
        "cross_source_edges": sum(is_cross_source(e, src) for e in edges) / max(len(edges), 1),
        "cross_source_typed_edges": sum(is_cross_source(e, src) for e in typed) / max(len(typed), 1),
        "entities_in_2plus_sources": sum(len(n["sources"]) >= 2 for n in ents),
        "entities_in_2plus_collections": sum(len([c for c in n["collections"] if c != "playbook"]) >= 2
                                             for n in ents),
        "typed_edges": len(typed),
        "typed_by_method": dict(Counter(e["method"] for e in typed)),
        "relations": dict(Counter(e["relation"] for e in edges)),
        "types": dict(Counter(n["type"] for n in nodes)),
        "communities": len(g.get("communities", [])),
        "communities_over_20": sum(c["size"] > 20 for c in g.get("communities", [])),
    }


def before_metrics() -> dict:
    """The previous graph (export.build_full) for both collections, measured the same way."""
    from knowledge.graph.export import build_full
    names, kinds, two_src, n_nodes, n_edges, deg0 = [], [], 0, 0, 0, 0
    for coll in ("dev_docs", "git_docs"):
        g = build_full(coll)
        ents = [n for n in g["nodes"] if n["type"] == "entity"]
        names += [n["label"].lower() for n in ents]
        kinds += [f"{n.get('kind')}|{n['label'].lower()}" for n in ents]
        two_src += sum(n.get("sources", 0) >= 2 for n in ents)
        n_nodes += len(g["nodes"])
        n_edges += len(g["edges"])
        deg0 += sum(n["degree"] == 0 for n in g["nodes"])
    rnd = random.Random(SEED)
    sample = rnd.sample(names, min(300, len(names)))
    dup, pairs = duplicate_rate(names, sample)
    dup_t, _ = duplicate_rate(kinds, random.Random(SEED).sample(kinds, min(300, len(kinds))))
    return {"duplicate_rate_same_type": dup_t, "nodes": n_nodes, "edges": n_edges, "entities": len(names), "duplicate_rate": dup,
            "duplicate_examples": pairs[:12], "entities_in_2plus_sources": two_src,
            "typed_edges": 0, "edges_with_evidence": 0.0, "orphan_ratio": deg0 / max(n_nodes, 1),
            "avg_degree": 2 * n_edges / max(n_nodes, 1)}


def pct(x) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def report(after: dict, before: dict | None) -> str:
    rows = [("nodes", "nodes", str), ("edges", "edges", str), ("entity nodes", "entities", str),
            ("orphan ratio", "orphan_ratio", pct), ("average degree", "avg_degree", lambda v: f"{v:.2f}"),
            ("edges with evidence", "edges_with_evidence", pct),
            ("duplicate-looking names (300-name sample, any type)", "duplicate_rate", pct),
            ("duplicate-looking names within one type", "duplicate_rate_same_type", pct),
            ("entities found in 2+ sources", "entities_in_2plus_sources", str),
            ("typed entity-to-entity edges", "typed_edges", str)]
    out = ["| metric | before (rule entities, no provenance) | after |", "|---|---|---|"]
    for label, key, fmt in rows:
        b = fmt(before[key]) if before and key in before else "n/a"
        out.append(f"| {label} | {b} | {fmt(after[key])} |")
    out += ["", "| after only | value |", "|---|---|",
            f"| evidence items whose chunk and document exist | {pct(after['evidence_chunks_exist'])} |",
            f"| cross-source edges (all) | {pct(after['cross_source_edges'])} |",
            f"| cross-source edges (typed only) | {pct(after['cross_source_typed_edges'])} |",
            f"| entities in 2+ collections | {after['entities_in_2plus_collections']} |",
            f"| communities (over 20 members) | {after['communities']} ({after['communities_over_20']}) |",
            f"| typed edges by method | {after['typed_by_method']} |",
            f"| edges by relation | {after['relations']} |"]
    return "\n".join(out)


# ---- edge review -------------------------------------------------------------------------

def review_sample(g: dict, n: int = 40) -> list[dict]:
    """n edges, stratified: half typed relations (the ones that claim knowledge), the rest
    spread over mentions and options. Seeded, so the file can be regenerated identically."""
    rnd = random.Random(SEED)
    typed = [e for e in g["edges"] if e["family"] in ("troubleshooting", "docs", "command")
             and e["relation"] != "HAS_SECTION"]
    other = [e for e in g["edges"] if e["relation"] == "MENTIONS"]
    pick = rnd.sample(typed, min(n // 2, len(typed)))
    pick += rnd.sample(other, min(n - len(pick), len(other)))
    return pick


def write_review(g: dict, n: int, path: Path = REVIEW) -> Path:
    names = {x["id"]: x["name"] for x in g["nodes"]}
    lines = ["# Graph edge review", "",
             "For each edge: does the quote support the relation? Replace `?` with `y` or `n` in the",
             "last column (one letter). Then run `python -m knowledge.graph.quality --precision`.",
             "This file holds short quotes from the corpus (see each source's license in the viewer); it is git-ignored.",
             "", "| # | edge id | relation | from | to | method | quote | correct |", "|---|---|---|---|---|---|---|---|"]
    for i, e in enumerate(review_sample(g, n), 1):
        ev = e["evidence"][0]
        q = ev["quote"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | {e['id']} | {e['relation']} | {names[e['source']][:50]} | "
                     f"{names[e['target']][:50]} | {e['method']} | {q} | ? |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def precision(path: Path = REVIEW) -> dict:
    marks = Counter()
    by_method = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 8 and cells[0].isdigit():
            mark = cells[-1].lower()
            marks[mark] += 1
            by_method.setdefault(cells[5], Counter())[mark] += 1
    judged = marks["y"] + marks["n"]
    return {"judged": judged, "unmarked": marks["?"], "precision": marks["y"] / judged if judged else None,
            "by_method": {m: (c["y"] / (c["y"] + c["n"]) if c["y"] + c["n"] else None) for m, c in by_method.items()}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", type=int, default=0, help="write this many edges to eval/graph_edge_review.md")
    ap.add_argument("--precision", action="store_true")
    ap.add_argument("--no-before", action="store_true", help="skip rebuilding the previous graph")
    a = ap.parse_args()
    if a.precision:
        print(precision())
        return
    g = load_graph()
    chunk_ids = {c["id"] for c in load_chunks()}
    doc_ids = {d["id"] for d in load_documents()}
    after = metrics(g, chunk_ids, doc_ids)
    before = None if a.no_before else before_metrics()
    md = report(after, before)
    ex = "\n".join(f"- {x} / {y}" for x, y in after["duplicate_examples"])
    exb = "\n".join(f"- {x} / {y}" for x, y in (before or {}).get("duplicate_examples", []))
    REPORT.write_text("# Knowledge graph quality\n\nGenerated by `python -m knowledge.graph.quality`. Numbers only;"
                      " no document text.\n\n" + md + "\n\n## Remaining duplicate-looking names (after)\n\n"
                      + (ex or "none") + "\n\n## Duplicate-looking names before\n\n" + (exb or "n/a") + "\n",
                      encoding="utf-8")
    print(md)
    print(f"\nwrote {REPORT}")
    if a.review:
        print(f"wrote {write_review(g, a.review)}")


if __name__ == "__main__":
    main()
