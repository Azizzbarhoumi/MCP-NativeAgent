"""Grade an existing run's saved answers again with another judge (no new retrieval or answers).
Shows how often two judges disagree, so we know how far a judge's numbers can be trusted.

Usage: python -m eval.rejudge eval/results/knowledge_baseline_v2_....json --judge groq
Writes the same file name with _judge-<name> added, which eval.compare can read.
"""

import argparse
import json
from pathlib import Path

from common.schemas import new_id
from common.tracing import flush
from eval.metrics import summarize, table
from eval.run_knowledge_eval import JUDGES, judge


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--judge", default="groq", choices=sorted(JUDGES))
    a = ap.parse_args()

    src = Path(a.results)
    run = json.loads(src.read_text(encoding="utf-8"))
    old_judge = run.get("judge", "mistral")
    rows, changed = [], []
    for r in run["rows"]:
        verdict, reason, tokens = judge(r["question"], r["expected"], r["answer"], new_id(), a.judge)
        if verdict != r["verdict"]:
            changed.append(f"- **{r['id']}**: {old_judge} said {r['verdict']}, {a.judge} says {verdict}: {reason}")
        print(f"{r['id']}: {r['verdict']:9} -> {verdict:9} {reason[:90]}")
        rows.append({**r, "verdict": verdict, "judge_reason": reason, "judge_tokens": tokens,
                     f"verdict_{old_judge}": r["verdict"]})

    agree = 1 - len(changed) / max(len(rows), 1)
    run.update(judge=a.judge, rows=rows, summary=summarize(rows), rejudged_from=src.name)
    label = f"{run['mode']}+{run.get('prompt', 'v1')} (judge {a.judge})"
    out = src.with_name(f"{src.stem}_judge-{a.judge}.json")
    out.write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
    report = (f"{table(run['summary'], label)}\n\n**Judge agreement {old_judge} vs {a.judge}: {agree:.0%}** "
              f"({len(changed)} of {len(rows)} verdicts changed)\n\n" + ("\n".join(changed) or "(none)"))
    out.with_suffix(".md").write_text(f"# Re-judged: {src.name}\n\n{report}\n", encoding="utf-8")
    print("\n" + report + f"\n\nsaved {out}")
    flush()


if __name__ == "__main__":
    main()
