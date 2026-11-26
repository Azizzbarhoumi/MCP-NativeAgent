"""Average repeated benchmark runs, because one run is noisy (about 1-2 questions run to run).

Groups result files by setting and prints mean, min and max for each group:
  knowledge runs  setting = mode + prompt tag (check/decomp) + answer model + judge
  git e2e runs    setting = tag + answer model + judge, tuned and held-out kept apart

Usage:
  python -m eval.average_runs eval/results/knowledge_*_20261126_*.json
  python -m eval.average_runs eval/results/git_e2e_*.json --out eval/results/final_benchmark.md

Earlier reference numbers (Mistral answers, Groq gpt-oss-120b judge, 31 questions) are printed
next to the knowledge table. They are only comparable when the answer model is the same.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from knowledge.answer import NOT_FOUND

REFERENCE = {"baseline+v1": 0.29, "baseline+v2": 0.74, "hybrid+v2": 0.71, "baseline+v2_check": 0.77}
REFERENCE_MODEL = "mistral (2026-11-23/24 runs)"


def pct(x: float) -> str:
    return f"{x:.0%}"


def stats(values: list[float]) -> str:
    if not values:
        return "n/a"
    if len(values) == 1:
        return pct(values[0])
    return f"{pct(mean(values))} ({pct(min(values))}-{pct(max(values))})"


def parse_pct(v) -> float | None:
    if isinstance(v, str) and v.endswith("%"):
        return float(v[:-1]) / 100
    return None


def knowledge_table(runs: list[dict]) -> list[str]:
    groups = defaultdict(list)
    for r in runs:
        groups[(f"{r['mode']}+{r.get('prompt', 'v1')}", r.get("answer_model", "mistral"), r.get("judge", "mistral"))].append(r)
    types = ("lookup", "exact_match", "multi_hop", "all")
    lines = ["## Knowledge benchmark (correct answers; mean and min-max over runs)", "",
             "| setting | answer model | judge | runs | " + " | ".join(types) + " | retrieval hit | wrong delivered | earlier (Mistral) |",
             "|---|---|---|---|" + "---|" * len(types) + "---|---|---|"]
    for (setting, model, judge), rs in sorted(groups.items()):
        cells = [stats([r["summary"][t]["correct"] for r in rs if t in r["summary"]]) for t in types]
        hit = stats([r["summary"]["all"]["retrieval_hit"] for r in rs])
        wrong = mean(sum(x["verdict"] == "incorrect" and not x["answer"].strip().startswith(NOT_FOUND)
                         for x in r["rows"]) for r in rs)
        ref = REFERENCE.get(setting)
        lines.append(f"| {setting} | {model} | {judge} | {len(rs)} | " + " | ".join(cells)
                     + f" | {hit} | {wrong:.1f}/{len(rs[0]['rows'])} | {pct(ref) if ref is not None else '-'} |")
    return lines + ["", f"The 'earlier' column is from {REFERENCE_MODEL}: only comparable for the same answer model."]


E2E_KEYS = ("passed", "diagnosis_correct", "repo_state_correct", "invariants_all_kept", "rubric_items_ok",
            "risk_tier_agreement")
E2E_SUMS = ("dangerous_without_backup", "risk_under_rated_steps", "usd_estimate_total")


def e2e_table(runs: list[dict]) -> list[str]:
    groups = defaultdict(list)
    for r in runs:
        s = r["summary"]
        groups[(s["tag"], s["answer_model"], s["judge"])].append(s)
    lines = ["## Git assistant end to end (mean and min-max over runs)", ""]
    for (tag, model, judge), ss in sorted(groups.items()):
        lines += [f"**{tag}**: answer model {model}, judge {judge}, {len(ss)} run file(s)", "",
                  "| metric | tuned | held-out |", "|---|---|---|"]
        for k in E2E_KEYS:
            row = []
            for part in ("tuned", "held_out"):
                vals = [v for v in (parse_pct(s[part].get(k)) for s in ss) if v is not None]
                row.append(stats(vals))
            lines.append(f"| {k} | {row[0]} | {row[1]} |")
        for k in E2E_SUMS:
            lines.append(f"| {k} (mean per run file) | {mean(s['tuned'].get(k, 0) for s in ss):.3g} | "
                         f"{mean(s['held_out'].get(k, 0) for s in ss):.3g} |")
        lines.append("")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", help="also write the markdown here")
    a = ap.parse_args()
    know, e2e = [], []
    for f in a.files:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if "mode" in d and "rows" in d:
            know.append(d)
        elif "summary" in d and "tuned" in d["summary"]:
            e2e.append(d)
        else:
            print(f"skipped (unknown format): {f}")
    md = ["# Averaged benchmark runs", ""]
    if know:
        md += knowledge_table(know) + [""]
    if e2e:
        md += e2e_table(e2e)
    text = "\n".join(md)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
