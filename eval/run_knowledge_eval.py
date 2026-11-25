"""Run the knowledge question set through retrieval + answer, judge each answer with an LLM,
and report metrics per question type.

Usage:
  python -m eval.run_knowledge_eval --mode baseline
  python -m eval.run_knowledge_eval --mode hybrid --prompt v2
  python -m eval.run_knowledge_eval --mode baseline --only ex01,mu03 --k 8
Writes eval/results/knowledge_<mode>_<prompt>_<timestamp>.json and .md
Compare two runs: python -m eval.compare <old.json> <new.json>
"""

import argparse
import os
import json
import re
import time
from datetime import datetime
from pathlib import Path

import yaml

from common import llm
from common.config import settings
from common.llm import chat
from common.schemas import new_id
from common.tracing import flush
from eval.metrics import retrieval_coverage, retrieval_hit, summarize, table
from knowledge.answer import DEFAULT_PROMPT, NOT_FOUND, PROMPTS, answer_question

EVAL = Path(__file__).resolve().parent
JUDGE_PROMPT = (EVAL / "judge_prompt.md").read_text(encoding="utf-8")


# The judge should not be the model that wrote the answers (models grade themselves kindly).
# Model names change as providers retire them, so the Groq one is overridable in .env.
JUDGES = {"mistral": ("mistral", "mistral-small-latest"),
          "groq": ("groq", os.environ.get("GROQ_JUDGE_MODEL") or settings.groq_judge_model)}


def judge(question: str, expected: str, answer: str, task_id: str,
          judge_name: str = "mistral") -> tuple[str, str, int]:
    # Rule, not LLM: an answer that opens with the refusal sentence is wrong for this set
    # (every question is answerable from the corpus). The LLM judge was seen to call
    # "The retrieved documents do not contain the answer ... 256 jobs" correct.
    if answer.strip().startswith(NOT_FOUND):
        return "incorrect", "declined: answer starts with the 'not found' sentence", 0
    before = llm.TOKENS.get(task_id, 0)
    raw = chat([{"role": "user", "content": JUDGE_PROMPT.format(
        question=question, expected=expected, answer=answer)}], task_id=task_id, agent="judge", temperature=0.0,
        provider=JUDGES[judge_name][0], model=JUDGES[judge_name][1])
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    verdict = data.get("verdict", "incorrect")
    if verdict not in ("correct", "partial", "incorrect"):
        verdict = "incorrect"
    return verdict, data.get("reason", f"unparseable judge output: {raw[:120]}"), llm.TOKENS.get(task_id, 0) - before


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="baseline")
    ap.add_argument("--k", type=int, default=None)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, choices=sorted(PROMPTS))
    # Default stays mistral so earlier runs remain comparable; use --judge groq from now on.
    ap.add_argument("--check", action="store_true", help="groundedness checker: retry, then refuse")
    ap.add_argument("--decompose", action="store_true", help="split questions that join topics")
    ap.add_argument("--judge", default="mistral", choices=sorted(JUDGES))
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--all", action="store_true", help="include questions not marked reviewed")
    a = ap.parse_args()

    questions = yaml.safe_load((EVAL / "knowledge_questions.yaml").read_text(encoding="utf-8"))
    if not a.all:
        questions = [q for q in questions if q.get("reviewed")]
    if a.only:
        ids = set(a.only.split(","))
        questions = [q for q in questions if q["id"] in ids]
    print(f"{len(questions)} questions, mode={a.mode}, prompt={a.prompt}, check={a.check}, decompose={a.decompose}, judge={a.judge}\n")

    rows, rows_skipped = [], []
    for q in questions:
        task_id = new_id()
        t0 = time.perf_counter()
        try:
            response, hits, timing = answer_question(q["question"], task_id, k=a.k, mode=a.mode, prompt=a.prompt, check=a.check, decomposition=a.decompose)
        except llm.ProviderAccessError as e:  # no key / no budget: every next question would fail too
            raise SystemExit(f"stopping: {e}")
        except Exception as e:  # one API failure must not lose the whole run
            print(f"[ERR ] {q['id']} skipped: {type(e).__name__}: {str(e)[:120]}\n")
            rows_skipped.append(q["id"])
            continue
        latency = time.perf_counter() - t0
        answer_tokens = llm.TOKENS.get(task_id, 0)  # judge tokens are counted separately

        retrieved = [f"{h.source}/{h.path}" for h in hits]
        expected = [d["path"] for d in q["source_docs"]]
        try:
            verdict, reason, judge_tokens = judge(q["question"], q["expected_answer"], response.answer, task_id, a.judge)
        except Exception as e:
            print(f"[ERR ] {q['id']} judge failed: {type(e).__name__}: {str(e)[:300]}\n")
            rows_skipped.append(q["id"])
            continue
        row = {
            "id": q["id"], "type": q["type"], "question": q["question"],
            "expected": q["expected_answer"], "answer": response.answer,
            "verdict": verdict, "judge_reason": reason,
            "hit": retrieval_hit(retrieved, expected), "coverage": retrieval_coverage(retrieved, expected),
            "retrieved": [{"chunk_id": h.chunk_id, "doc": f"{h.source}/{h.path}", "section": h.section,
                           "score": round(h.score, 4)} for h in hits],
            "expected_docs": expected, "cited": timing["cited"],
            "latency_s": round(latency, 2), "tokens": answer_tokens, "judge_tokens": judge_tokens,
            "task_id": task_id,
            "retried": timing.get("retried", False), "refused_by_checker": timing.get("refused_by_checker", False),
            "unsupported_first": timing.get("unsupported_first", []),
            "decomposed": timing.get("decomposed", False), "sub_questions": timing.get("sub_questions", []),
        }
        rows.append(row)
        mark = {"correct": "OK  ", "partial": "PART", "incorrect": "FAIL"}[verdict]
        print(f"[{mark}] {q['id']} hit={'Y' if row['hit'] else 'N'} cov={row['coverage']:.1f} "
              f"{latency:.1f}s  {q['question'][:70]}")
        print(f"        answer: {response.answer.strip()[:300].replace(chr(10), ' ')}")
        if timing.get("decomposed"):
            print(f"        split : {' | '.join(timing['sub_questions'])[:200]}")
        if timing.get("retried"):
            print(f"        check : retried ({'; '.join(timing.get('unsupported_first', []))[:150]})"
                  + (" -> REFUSED" if timing.get("refused_by_checker") else ""))
        print(f"        judge : {reason}\n")

    if not rows:
        # Every question failed (bad key, no budget, network): say so instead of crashing in table().
        raise SystemExit(f"0 questions answered; all {len(rows_skipped)} failed with API errors: {rows_skipped}. "
                         "Check the provider key/budget and re-run.")
    summary = summarize(rows)
    tag = a.prompt + ("_check" if a.check else "") + ("_decomp" if a.decompose else "")
    label = f"{a.mode}+{tag}"
    md = table(summary, label)
    if a.check:
        retried = [r for r in rows if r["retried"]]
        md += (f"\n\nChecker: retried {len(retried)}/{len(rows)}, refused {sum(r['refused_by_checker'] for r in rows)}, "
               f"correct after retry {sum(r['verdict'] == 'correct' for r in retried)}")
    if a.decompose:
        # Classifier quality: ideally every multi_hop question is split and nothing else is.
        mh = [r for r in rows if r["type"] == "multi_hop"]
        other = [r for r in rows if r["type"] != "multi_hop"]
        md += (f"\n\nDecomposer: split {sum(r['decomposed'] for r in mh)}/{len(mh)} multi_hop questions, "
               f"{sum(r['decomposed'] for r in other)}/{len(other)} others (should be 0)")
    # The number the checker exists to push down: confidently wrong answers that reached the user.
    # (Reported for every run, so checked and unchecked runs can be compared.)
    wrong = sum(r["verdict"] == "incorrect" and not r["answer"].strip().startswith(NOT_FOUND) for r in rows)
    md += f"\n\nWrong answers delivered (incorrect and not a refusal): {wrong}/{len(rows)}"
    failures = [r for r in rows if r["verdict"] != "correct" or not r["hit"]]
    fail_md = "\n".join(
        f"- **{r['id']}** ({r['type']}, {r['verdict']}, hit={r['hit']}, coverage={r['coverage']:.1f}): "
        f"{r['question']}\n  - expected: {r['expected']}\n  - got: {r['answer'].strip()[:400]}\n"
        f"  - judge: {r['judge_reason']}\n  - retrieved: {', '.join(x['doc'].split('/')[-1] for x in r['retrieved'])}"
        for r in failures)
    print(md + "\n\nFailures:\n" + (fail_md or "(none)"))
    if rows_skipped:
        print(f"\nSKIPPED (API errors, re-run with --only {','.join(rows_skipped)}): {rows_skipped}")

    out = EVAL / "results"
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = out / f"knowledge_{a.mode}_{tag}_{stamp}"
    answer_model = f"{settings.llm_provider}/{settings.llm_model}"
    base.with_suffix(".json").write_text(json.dumps({"mode": a.mode, "prompt": tag, "judge": a.judge, "k": a.k,
                                                     "answer_model": answer_model, "summary": summary, "rows": rows,
                                                     "skipped": rows_skipped},
                                                    indent=2, ensure_ascii=False), encoding="utf-8")
    base.with_suffix(".md").write_text(f"# Knowledge eval: {label}\n\nAnswer model: {answer_model}, judge: {a.judge}\n\n{md}\n\n## Failures\n\n{fail_md or '(none)'}\n",
                                       encoding="utf-8")
    print(f"\nsaved {base}.json / .md")
    flush()


if __name__ == "__main__":
    main()
