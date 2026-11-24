"""Run one question through the orchestrator.

Usage:
  python -m orchestrator.run "Do any open PRs have merge conflicts?" --repo owner/name
"""

import argparse
import asyncio

from common.schemas import new_id
from common.tracing import flush, get_langfuse, get_trace
from orchestrator.graph import ALLOWED_TOOLS, build_graph, initial_state
from orchestrator.mcp_client import github_tools


async def main(question: str, repo: str | None) -> None:
    if get_langfuse() is None:
        print("WARNING: Langfuse keys missing in .env; tracing is DISABLED for this run.")
    task_id = new_id()
    print(f"task_id: {task_id}")

    async with github_tools() as tools:
        catalog = await tools.describe(ALLOWED_TOOLS)  # real signatures from the server
        graph = build_graph(tools)
        state = await graph.ainvoke(initial_state(question, task_id, catalog, repo))

    print(f"\n{state['final_answer']}\n")
    print(f"Evidence ({len(state['evidence'])}):")
    for ev in state["evidence"]:
        print(f"  - {ev.text}  [{ev.source}]")
    print(f"Steps used: {state['step_count']}")

    trace = get_trace(task_id)
    if trace:  # one trace per run: record the question and final answer at the top level
        trace.update(input=question, output=state["final_answer"])
    flush()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("question")
    p.add_argument("--repo", help="owner/name of your test repo")
    a = p.parse_args()
    asyncio.run(main(a.question, a.repo))
