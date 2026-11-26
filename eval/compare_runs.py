"""Compare two knowledge runs question by question (Phase 5: A = v2 + checker, B = A + decompose).

  python -m eval.compare_runs A.json B.json [--out eval/results/compare_A_vs_B.md]
  (B may still be a .partial.jsonl: only the questions both runs answered are compared, and the
   report says so)

Why paired: both runs answer the same 31 questions, so what matters is which questions changed
verdict. The exact McNemar test looks only at those changed questions: with b questions only A
got right and c only B got right, it asks how likely a split at least that uneven is if neither
setting is better (a fair coin over b + c flips). One run per setting also carries run-to-run
noise: repeated runs of the SAME setting have differed by 1 to 2 questions, so a gap that small
is never called a win.

Refuses to compare runs whose answer model, judge, retrieval mode or k differ (the numbers would
mean nothing).
"""

import argparse
import json
from math import comb
from pathlib import Path

TYPES = ("lookup", "exact_match", "multi_hop")
NOISE_QUESTIONS = 2          # largest gap seen between repeated runs of one setting


def load(path: str) -> dict:
    p = Path(path)
    if p.suffix == ".jsonl":                      # a run still in progress (--resume file)
        lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        settings = lines[0].get("_settings", {}) if lines else {}
        return {**settings, "rows": lines[1:], "partial": True, "path": str(p)}
    d = json.loads(p.read_text(encoding="utf-8"))
    return {**d, "partial": False, "path": str(p)}


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value: binomial(b + c, 1/2), at least as uneven as observed."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(0, min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def wrong_delivered(row: dict) -> bool:
    from knowledge.answer import NOT_FOUND
    return row["verdict"] == "incorrect" and not row["answer"].strip().startswith(NOT_FOUND)


def compare(a: dict, b: dict) -> dict:
    for k in ("answer_model", "judge", "mode", "k"):
        if a.get(k) != b.get(k):
            raise ValueError(f"not comparable: {k} differs ({a.get(k)} vs {b.get(k)})")
    ra, rb = {r["id"]: r for r in a["rows"]}, {r["id"]: r for r in b["rows"]}
    common = [i for i in ra if i in rb]
    ok = lambda r: r["verdict"] == "correct"
    per_type = {}
    for t in TYPES + ("all",):
        ids = [i for i in common if t == "all" or ra[i]["type"] == t]
        per_type[t] = {"n": len(ids), "a": sum(ok(ra[i]) for i in ids), "b": sum(ok(rb[i]) for i in ids)}
    only_a = [i for i in common if ok(ra[i]) and not ok(rb[i])]
    only_b = [i for i in common if ok(rb[i]) and not ok(ra[i])]
    mean = lambda rows, k: sum(r.get(k, 0) for r in rows) / len(rows) if rows else 0.0
    out = {
        "common": len(common), "a_total": len(ra), "b_total": len(rb), "partial": a["partial"] or b["partial"],
        "per_type": per_type, "only_a": only_a, "only_b": only_b, "p": mcnemar_exact(len(only_a), len(only_b)),
        "wrong": {"a": sum(wrong_delivered(ra[i]) for i in common), "b": sum(wrong_delivered(rb[i]) for i in common)},
        "tokens": {"a": mean([ra[i] for i in common], "tokens"), "b": mean([rb[i] for i in common], "tokens")},
        "latency": {"a": mean([ra[i] for i in common], "latency_s"), "b": mean([rb[i] for i in common], "latency_s")},
        "split": {"multi_hop": sum(rb[i].get("decomposed", False) for i in common if rb[i]["type"] == "multi_hop"),
                  "others": sum(rb[i].get("decomposed", False) for i in common if rb[i]["type"] != "multi_hop")},
        "types": {i: ra[i]["type"] for i in common},
    }
    out["verdict"] = verdict(out)
    return out


def verdict(c: dict) -> str:
    gap = len(c["only_b"]) - len(c["only_a"])
    partial = (f" Only {c['common']} of {max(c['a_total'], c['b_total'])} questions are in both runs, so this is "
               f"provisional.") if c["partial"] or c["common"] < max(c["a_total"], c["b_total"]) else ""
    if not c["only_a"] and not c["only_b"]:
        return "No question changed verdict: the two settings scored the same on this pair." + partial
    who = "B (decomposition)" if gap > 0 else "A (no decomposition)" if gap < 0 else "neither"
    if abs(gap) <= NOISE_QUESTIONS or c["p"] >= 0.05:
        lean = (f"one pair cannot show that {who} is better" if gap else "the changes cancel out")
        return (f"Within noise. {len(c['only_b'])} question(s) improved with decomposition and {len(c['only_a'])} got "
                f"worse (net {gap:+d}; exact McNemar p = {c['p']:.2f}). Repeated runs of one setting already differ by "
                f"1 to {NOISE_QUESTIONS} questions, so {lean}. Keep decomposition off unless a second pair agrees."
                + partial)
    return (f"{who} is better on this pair: net {gap:+d} questions, exact McNemar p = {c['p']:.3f}, beyond the "
            f"1 to {NOISE_QUESTIONS} question run-to-run noise. Confirm with a second pair before changing the "
            f"default." + partial)


def markdown(a: dict, b: dict, c: dict) -> str:
    pct = lambda x, n: f"{x}/{n} ({x / n:.0%})" if n else "-"
    named = lambda ids: ", ".join(f"{i} ({c['types'][i]})" for i in ids) or "none"
    lines = [f"# Phase 5: A vs B, question by question", "",
             f"- A: `{Path(a['path']).name}`", f"- B: `{Path(b['path']).name}`" + (" (still running)" if b["partial"] else ""),
             f"- Answer model {a.get('answer_model')}, judge {a.get('judge')} (same for both).",
             f"- Compared: {c['common']} questions answered by both.", "",
             "| type | n | A correct | B correct | change |", "|---|---|---|---|---|"]
    for t in TYPES + ("all",):
        s = c["per_type"][t]
        lines.append(f"| {t} | {s['n']} | {pct(s['a'], s['n'])} | {pct(s['b'], s['n'])} | {s['b'] - s['a']:+d} |")
    lines += ["", f"- Wrong answers delivered (incorrect and not a refusal): A {c['wrong']['a']}, B {c['wrong']['b']}.",
              f"- Answer tokens per question: A {c['tokens']['a']:.0f}, B {c['tokens']['b']:.0f} "
              f"({(c['tokens']['b'] / c['tokens']['a'] - 1) if c['tokens']['a'] else 0:+.0%}).",
              f"- Latency per question: A {c['latency']['a']:.1f} s, B {c['latency']['b']:.1f} s.",
              f"- Decomposer split {c['split']['multi_hop']} multi_hop question(s) and {c['split']['others']} other(s) "
              f"(ideally all multi_hop and no others).",
              f"- Better only with B: {named(c['only_b'])}.",
              f"- Better only with A: {named(c['only_a'])}.",
              "", f"**Verdict:** {c['verdict']}", ""]
    return "\n".join(lines)


def main(argv=None) -> None:
    from common.console import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    a, b = load(args.a), load(args.b)
    try:
        text = markdown(a, b, compare(a, b))
    except ValueError as e:
        raise SystemExit(str(e))
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
