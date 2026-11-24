"""Ask the LLM your own questions. Each question is its own traced task in Langfuse.

Usage:
  python ask.py "What is MCP?"      one question
  python ask.py                     interactive chat (type 'exit' to quit)
"""

import sys

from common.llm import chat
from common.schemas import new_id
from common.tracing import flush


def ask(question: str) -> None:
    task_id = new_id()
    answer = chat([{"role": "user", "content": question}], task_id=task_id)
    print(f"\n{answer}\n\n(trace: task-{task_id[:8]})\n")


if len(sys.argv) > 1:
    ask(" ".join(sys.argv[1:]))
else:
    while (q := input("You: ").strip()) not in ("exit", "quit"):
        if q:
            ask(q)
flush()
