"""Groundedness checker: is every claim in the answer backed by the retrieved passages?

Unlike the eval judge (which only grades, offline, against a reference answer), this runs
INSIDE the pipeline, with no reference answer, and its verdict changes what the user gets:
retry with more evidence, or refuse. It uses a different model from both the answerer and
the judge, so the judge never grades a decision made by its own model.
"""

import json
import re

from pydantic import BaseModel

from common.config import settings
from common.llm import chat

PROMPT = """You verify an answer against source passages. You do not know the true answer;
judge ONLY whether the passages back what the answer says.

Rules:
- A claim is supported if a passage states it or directly implies it.
- Option names, commands, numbers and setting names must appear in a passage.
- Sentences saying what the passages do NOT contain are not claims; ignore them.
- Citation numbers do not matter, only content.
- Also decide if the answer actually ANSWERS the question. It does not if its main claim is
  about something else, or does the opposite of what was asked (for example a command that
  disables the feature the question asks how to enable), even when that claim is quoted
  from a passage.

Passages:
{context}

Question: {question}

Answer to verify:
{answer}

Reply with JSON only:
{{"grounded": true or false,
  "answers_question": true or false,
  "why_not_answering": "one sentence, or empty",
  "unsupported": ["each claim that no passage backs, quoted briefly"],
  "missing_query": "a short search query for the information the answer still needs, or empty"}}"""


class Check(BaseModel):
    grounded: bool
    answers_question: bool = True
    why_not_answering: str = ""
    unsupported: list[str] = []
    missing_query: str = ""

    @property
    def ok(self) -> bool:
        """Deliverable only if every claim is backed AND the answer addresses the question."""
        return self.grounded and self.answers_question

    @property
    def problems(self) -> list[str]:
        extra = [f"does not answer the question: {self.why_not_answering or 'off-topic'}"] \
            if not self.answers_question else []
        return self.unsupported + extra


def check(question: str, context: str, answer: str, task_id: str) -> Check:
    raw = chat([{"role": "user", "content": PROMPT.format(context=context, question=question, answer=answer)}],
               task_id=task_id, agent="checker", temperature=0.0,
               provider=settings.checker_provider, model=settings.checker_model)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return Check.model_validate(json.loads(m.group(0)))
    except (AttributeError, ValueError):
        # Fail closed: an unreadable verdict counts as "not verified", never as "fine".
        return Check(grounded=False, unsupported=[f"checker output unreadable: {raw[:80]}"])
