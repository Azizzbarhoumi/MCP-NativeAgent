"""Retrieval-only A/B: no LLM, no judge, zero tokens. Compares retrieval modes on the knowledge
question set (tuned and held-out halves reported apart, per question type) and on the
git_docs probe questions, with latency.

  python -m eval.run_retrieval_eval --modes baseline,hybrid,graph_dense,graph

Needs Qdrant with dev_docs and git_docs, the embedding model, and for the graph modes
knowledge/graph/data/graph.json built from the SAME corpus (python -m knowledge.graph.provenance
then python -m knowledge.graph.build). Writes eval/results/retrieval_<stamp>.json and .md.
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import yaml

from eval.metrics import retrieval_coverage, retrieval_hit

EVAL = Path(__file__).resolve().parent


def split_ids() -> set[str]:
    d = yaml.safe_load((EVAL / "knowledge_split.yaml").read_text(encoding="utf-8"))
    return {i for ids in d["held_out"].values() for i in ids}


def probes() -> list[tuple[str, list[str]]]:
    from tests.test_git_docs_retrieval import PROBES
    return PROBES


def run(modes: list[str], k: int = 5) -> dict:
    from knowledge.retrieve import search
    qs = [q for q in yaml.safe_load((EVAL / "knowledge_questions.yaml").read_text(encoding="utf-8"))
          if q.get("reviewed")]
    held = split_ids()
    if any(m.startswith("graph") for m in modes):
        from knowledge.graph.retrieval import index
        t0 = time.perf_counter()
        index()
        graph_load = time.perf_counter() - t0
    else:
        graph_load = None
    for m in modes:                                   # warm-up: model loading is not query latency
        search("warm up", k=k, collection="dev_docs", mode=m)
    rows = []
    for q in qs:
        expected = [d["path"] for d in q["source_docs"]]
        for m in modes:
            t0 = time.perf_counter()
            hits = search(q["question"], k=k, collection="dev_docs", mode=m)
            dt = time.perf_counter() - t0
            got = [f"{h.source}/{h.path}" for h in hits]
            rows.append({"set": "knowledge", "id": q["id"], "type": q["type"],
                         "split": "held_out" if q["id"] in held else "tuned", "mode": m,
                         "hit": retrieval_hit(got, expected), "coverage": retrieval_coverage(got, expected),
                         "latency_ms": round(dt * 1000, 1), "retrieved": [h.chunk_id for h in hits]})
    for i, (question, frags) in enumerate(probes()):
        for m in modes:
            t0 = time.perf_counter()
            hits = search(question, k=k, collection="git_docs", mode=m)
            dt = time.perf_counter() - t0
            ok = any(f in h.path for h in hits for f in frags)
            rows.append({"set": "git_probe", "id": f"probe{i + 1}", "type": "git_probe", "split": "probe",
                         "mode": m, "hit": ok, "coverage": 1.0 if ok else 0.0, "latency_ms": round(dt * 1000, 1),
                         "retrieved": [h.chunk_id for h in hits]})
    return {"modes": modes, "k": k, "graph_load_s": graph_load, "rows": rows}


def table(res: dict) -> str:
    modes = res["modes"]
    out = ["| set | split | type | n | " + " | ".join(f"{m} hit / full cov" for m in modes) + " |",
           "|---|---|---|---|" + "---|" * len(modes)]
    groups = {}
    for r in res["rows"]:
        for key in [(r["set"], r["split"], r["type"]), (r["set"], r["split"], "all")]:
            groups.setdefault(key, {}).setdefault(r["mode"], []).append(r)
    for key in sorted(groups, key=lambda x: (x[0] != "knowledge", x[1], x[2] == "all", x[2])):
        if key[0] == "git_probe" and key[2] == "all":
            continue
        cells, n = [], 0
        for m in modes:
            rs = groups[key].get(m, [])
            n = len(rs)
            cells.append(f"{sum(r['hit'] for r in rs)}/{n} / {sum(r['coverage'] == 1.0 for r in rs)}/{n}")
        out.append(f"| {key[0]} | {key[1]} | {key[2]} | {n} | " + " | ".join(cells) + " |")
    lat = [f"{m}: {sorted(r['latency_ms'] for r in res['rows'] if r['mode'] == m)[len(res['rows']) // len(modes) // 2]} ms"
           for m in modes]
    out.append("")
    out.append("Median latency per query: " + ", ".join(lat) +
               (f". Graph loaded once in {res['graph_load_s']:.1f} s." if res["graph_load_s"] else ""))
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="baseline,hybrid,graph_dense,graph")
    ap.add_argument("--k", type=int, default=5)
    a = ap.parse_args()
    res = run(a.modes.split(","), a.k)
    md = table(res)
    print(md)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = EVAL / "results" / f"retrieval_{stamp}"
    base.with_suffix(".json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    base.with_suffix(".md").write_text(f"# Retrieval-only A/B (k={a.k})\n\n{md}\n", encoding="utf-8")
    print(f"\nsaved {base}.json / .md")


if __name__ == "__main__":
    main()
