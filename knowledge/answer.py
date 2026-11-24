"""Answer a question from retrieved chunks ONLY, with citations. One Langfuse trace per question:
a `retrieval` span (what was found, with scores) and the LLM `generation` (what was answered)."""

import re
import time

from common.llm import chat
from common.schemas import AgentResponse, Evidence
from common.tracing import get_trace
from knowledge import decompose, grounding
from knowledge.retrieve import Hit, multi_search, search

NOT_FOUND = "The retrieved documents do not contain the answer."

# v1 = baseline prompt (kept so old results stay reproducible).
# v2 = step 4b: the baseline eval showed 18 refusals where the answer WAS in the passages,
# so v2 refuses only when nothing relevant was retrieved, and answers partly otherwise.
PROMPTS = {
    "v1": """Answer the question using ONLY the numbered context passages below.
Rules:
- Cite every claim with the passage number in square brackets, e.g. [2] or [1][3].
- Quote exact option names, commands and error messages as they appear.
- If the passages do not contain the answer, reply exactly: "{not_found}" and then one
  sentence on what is missing. Do not use outside knowledge.

Context:
{context}

Question: {question}""",
    "v2": """Answer the question using ONLY the numbered context passages below.
Rules:
- Cite every claim with the passage number in square brackets, e.g. [2] or [1][3].
- Quote exact option names, commands and error messages as they appear.
- The passages may word things differently from the question. A value in a table, an
  option in a code example or a sentence that implies the answer all count as support.
- If the passages answer only part of the question, answer that part and then say in one
  sentence what is missing. Do not refuse in that case.
- Only if NO passage is relevant to the question, reply exactly: "{not_found}"
- Never use outside knowledge: every fact must come from a cited passage.

Context:
{context}

Question: {question}""",
}
DEFAULT_PROMPT = "v2"


def format_context(hits: list[Hit]) -> str:
    return "\n\n".join(
        f"[{i}] (source: {h.source}/{h.path} | section: {h.section or '-'})\n{h.text}"
        for i, h in enumerate(hits, 1)
    )


def cited_numbers(answer: str, n_hits: int) -> list[int]:
    """Passage numbers the model actually cited, in order, ignoring out-of-range numbers."""
    seen = []
    for m in re.findall(r"\[(\d+)\]", answer):
        i = int(m)
        if 1 <= i <= n_hits and i not in seen:
            seen.append(i)
    return seen


FEEDBACK = """

A reviewer checked an earlier answer against the passages and found these problems:
{claims}
More passages were added above. Answer again: keep only what the passages support, and say
what is still missing instead of guessing."""


def _generate(question: str, hits: list[Hit], task_id: str, prompt: str, feedback: str = "") -> str:
    content = PROMPTS[prompt].format(not_found=NOT_FOUND, context=format_context(hits), question=question)
    return chat([{"role": "user", "content": content + feedback}], task_id=task_id, agent="knowledge",
                temperature=0.0)


def _merge(first: list[Hit], extra: list[Hit]) -> list[Hit]:
    seen = {h.chunk_id for h in first}
    return first + [h for h in extra if h.chunk_id not in seen]


def answer_question(question: str, task_id: str, k: int | None = None, mode: str = "baseline",
                    prompt: str = DEFAULT_PROMPT, check: bool = False,
                    decomposition: bool = False, collection: str | None = None) -> tuple[AgentResponse, list[Hit], dict]:
    """Returns (response with cited evidence, all hits shown to the model, info).

    check=True adds the groundedness loop:
      answer -> checker. Not grounded? -> search again for what is missing (hybrid), re-answer
      with the checker's feedback -> checker again. Still not grounded? -> refuse.
    At most one retry, so cost is bounded: worst case 2 answers + 2 checks.
    """
    trace = get_trace(task_id, agent="knowledge", mode=mode, prompt=prompt, check=check, decomposition=decomposition)
    span = trace.span(name=f"retrieval.{mode}", input={"question": question, "k": k}) if trace else None

    t0 = time.perf_counter()
    sub_questions: list[str] = []
    if decomposition:
        p = decompose.plan(question, task_id)
        sub_questions = p.sub_questions
    # Joined topics: original question + one search per topic, merged so each topic is present.
    hits = multi_search([question, *sub_questions], k=k, mode=mode, collection=collection) if sub_questions else search(question, k=k, collection=collection, mode=mode)
    t_retrieval = time.perf_counter() - t0
    if span:
        span.end(output=[{"chunk_id": h.chunk_id, "score": round(h.score, 4),
                          "doc": f"{h.source}/{h.path}", "section": h.section} for h in hits])

    t1 = time.perf_counter()
    text = _generate(question, hits, task_id, prompt)
    info: dict = {"checked": check, "retried": False, "refused_by_checker": False,
                  "decomposed": bool(sub_questions), "sub_questions": sub_questions}

    if check and not text.strip().startswith(NOT_FOUND):
        c1 = grounding.check(question, format_context(hits), text, task_id)
        info.update(grounded_first=c1.ok, unsupported_first=c1.problems)
        if not c1.ok:
            info["retried"] = True
            extra = search(c1.missing_query or question, k=k, collection=collection, mode="hybrid")
            hits = _merge(hits, extra)
            feedback = FEEDBACK.format(claims="\n".join(f"- {u}" for u in c1.problems) or "- (unspecified)")
            text = _generate(question, hits, task_id, prompt, feedback)
            if not text.strip().startswith(NOT_FOUND):
                c2 = grounding.check(question, format_context(hits), text, task_id)
                info.update(grounded_final=c2.ok, unsupported_final=c2.problems)
                if not c2.ok:
                    # Better no answer than an unsupported one.
                    info["refused_by_checker"] = True
                    text = (f"{NOT_FOUND} The draft answer could not be verified: "
                            + "; ".join(c2.problems[:3]))
    t_answer = time.perf_counter() - t1

    cited = cited_numbers(text, len(hits))
    evidence = [Evidence(text=hits[i - 1].text[:300], source=hits[i - 1].url or f"{hits[i - 1].source}/{hits[i - 1].path}",
                         tool=f"knowledge.{mode}") for i in cited]
    # Confidence here is only "did it find support": 0 if it declined or cited nothing.
    confidence = 0.0 if text.strip().startswith(NOT_FOUND) or not cited else 1.0
    response = AgentResponse(answer=text, evidence=evidence, confidence=confidence)
    if trace:
        trace.update(input=question, output=text)
    return response, hits, {"retrieval_s": t_retrieval, "answer_s": t_answer, "cited": cited, **info}
