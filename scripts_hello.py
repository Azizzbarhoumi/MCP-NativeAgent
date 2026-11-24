"""Lesson 5 smoke test: one traced LLM call. Run: python scripts_hello.py"""

from common.llm import chat
from common.schemas import new_id
from common.tracing import flush

task_id = new_id()
print("task_id:", task_id)
print(chat([{"role": "user", "content": "Say hello in French, English and Arabic."}], task_id=task_id))
flush()
print("Now open http://localhost:3000 -> Traces")
