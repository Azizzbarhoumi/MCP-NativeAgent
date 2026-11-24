"""Query decomposition: split a question that joins DIFFERENT topics into one search per topic.

Why: one embedding of "pytest exit code + GitHub step setting" lands near pytest docs only,
so the GitHub half is never retrieved (mu01, mu03, mu05, mu08 in the baseline eval).
Searching each sub-question separately gives every topic its own top results.

One LLM call does both jobs: classify (does it join topics?) and split. Single-topic
questions are left alone, so they pay only the classifier call, not extra searches.
"""

import json
import re

from pydantic import BaseModel

from common.config import settings
from common.llm import chat

PROMPT = """You prepare searches over technical documentation (CI, testing, linting, Docker,
web frameworks, task queues). Decide if answering the question needs facts about two or more
DIFFERENT tools or topics that are documented in different places, for example "a pytest
exit code" AND "a GitHub Actions step setting".

- Two facts about the SAME tool (e.g. two Celery settings) do NOT count: joins_topics=false.
- If joins_topics is true, write 2 or 3 short, self-contained search queries, one per topic.
  Each must make sense on its own (name the tool), and together they must cover the question.

Question: {question}

Reply with JSON only: {{"joins_topics": true or false, "sub_questions": ["...", "..."]}}"""


class Plan(BaseModel):
    joins_topics: bool = False
    sub_questions: list[str] = []


def plan(question: str, task_id: str) -> Plan:
    raw = chat([{"role": "user", "content": PROMPT.format(question=question)}], task_id=task_id,
               agent="decomposer", temperature=0.0,
               provider=settings.decomposer_provider, model=settings.decomposer_model)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        p = Plan.model_validate(json.loads(m.group(0)))
    except (AttributeError, ValueError):
        return Plan()  # unreadable -> behave exactly like no decomposition
    subs = [s.strip() for s in p.sub_questions if s.strip()][:3]
    # A "split" into fewer than 2 queries is not a split.
    split = p.joins_topics and len(subs) >= 2
    return Plan(joins_topics=split, sub_questions=subs if split else [])
