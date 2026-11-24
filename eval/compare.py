"""Side-by-side comparison of two or more knowledge eval runs, per question type, plus the
questions whose verdict changed between the first and the last run.

Usage: python -m eval.compare eval/results/knowledge_baseline_....json eval/results/knowledge_hybrid_v2_....json
"""

import json
import sys
from pathlib import Path

RANK = {"incorrect": 0, "partial": 1, "correct": 2}


def label(run: dict) -> str:
    # runs made before 4b used prompt v1 and the mistral judge
    return f"{run['mode']}+{run.get('prompt', 'v1')} (judge {run.get('judge', 'mistral')})"


def compare(runs: list[dict]) -> str:
    types = [t for t in ("lookup", "exact_match", "multi_hop") if t in runs[0]["summary"]] + ["all"]
    out = []
    for metric, name in [("correct", "correct"), ("retrieval_hit", "retrieval hit"), ("full_coverage", "full coverage")]:
        out += [f"\n**{name}**\n", "| type | " + " | ".join(label(r) for r in runs) + " |",
                "|---|" + "---|" * len(runs)]
        for t in types:
            out.append(f"| {t} | " + " | ".join(f"{r['summary'].get(t, {}).get(metric, 0):.0%}" for r in runs) + " |")

    first = {r["id"]: r for r in runs[0]["rows"]}
    last = {r["id"]: r for r in runs[-1]["rows"]}
    flips = []
    for qid in sorted(first.keys() & last.keys()):
        a, b = first[qid], last[qid]
        if a["verdict"] != b["verdict"] or a["hit"] != b["hit"]:
            arrow = "fixed" if RANK[b["verdict"]] > RANK[a["verdict"]] else (
                "broke" if RANK[b["verdict"]] < RANK[a["verdict"]] else "same verdict")
            flips.append(f"- **{qid}** ({a['type']}): {a['verdict']} -> {b['verdict']} ({arrow}), "
                         f"hit {a['hit']} -> {b['hit']}, coverage {a['coverage']:.1f} -> {b['coverage']:.1f}")
    out += [f"\n**Changed questions ({label(runs[0])} -> {label(runs[-1])})**\n"] + (flips or ["(none)"])
    return "\n".join(out)


if __name__ == "__main__":
    runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in sys.argv[1:]]
    if len(runs) < 2:
        sys.exit("give at least two result .json files")
    print(compare(runs))
