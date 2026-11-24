# Agent Platform: MCP-native multi-agent system with tool routing, safe actions and evaluation

> Instructions for Claude Code: read this whole file first. Your first task is to create the folder structure and placeholder files described in "Repository layout", then implement the services in the order given in "Build order". Ask before deviating from the architecture.

## 1. Project goal

A self-hosted, model-agnostic agent platform. A user chats with one assistant that can use many tools (GitHub, calendar, web search, personal documents). The project exists to show engineering depth in four areas and to measure them:

1. Semantic tool routing: give the model only the 5-8 relevant tools out of 50+.
2. Multi-agent delegation over A2A with a verifier agent.
3. Agentic RAG + GraphRAG over the user's documents.
4. Safe actions: human approval for writes, scoped credentials, prompt-injection checks.

A 40-task benchmark compares configurations (all tools in prompt vs routed tools; single agent vs multi-agent; naive RAG vs hybrid vs GraphRAG vs agentic GraphRAG). Results go in `eval/results/` and the top of this README.

Target timeline: about 9 days for a working version. Cut A2A to two agents before cutting evaluation.

## 2. Architecture overview

```
User -> Chat UI -> Orchestrator agent
                     |-- Memory (short-term checkpoint + long-term vector store)
                     |-- Safety layer (permissions, approval gate, injection check)
                     |-- A2A -> Research agent   -> MCP: web search, fetch
                     |-- A2A -> Knowledge agent  -> MCP: custom knowledge server -> Qdrant + Neo4j
                     |-- A2A -> Action agent     -> MCP: GitHub, Calendar (writes behind approval)
                     |-- A2A -> Verifier agent   -> checks answer vs evidence
                     '-- Tool router -> picks the MCP tools each agent may see
Everything traced in Langfuse.
```

Agents (5): Orchestrator, Research, Knowledge, Action, Verifier.
Components (not agents): Tool router, Safety layer, Memory, Observability.

### Shared contracts

- Every agent runs as its own service and publishes an A2A agent card (name, description, skills, endpoint).
- Every task carries a `task_id`. All traces and logs use it.
- Shared evidence format returned by every agent:

```json
{
  "answer": "string",
  "evidence": [{"text": "string", "source": "url|doc_id", "tool": "string"}],
  "confidence": 0.0
}
```

- Action proposals are typed objects:

```json
{
  "tool": "github.create_issue",
  "arguments": {},
  "risk": "read|write|destructive",
  "preview": "human-readable description",
  "idempotency_key": "uuid"
}
```

## 3. Agent specifications

### 3.1 Orchestrator
- LangGraph supervisor. Nodes: `load_memory -> plan -> delegate -> collect -> verify -> synthesize -> save_memory`.
- Planner outputs a structured list of steps (target agent, expected result). Step limit 8, retry limit 2.
- On agent timeout or verifier rejection: replan once, then answer with what is known and state what is missing.
- Parallel delegation when steps are independent.

### 3.2 Research agent
- MCP tools: web search (Brave, Tavily or DuckDuckGo-based server) and a fetch server.
- Flow: decompose question -> parallel searches -> fetch and extract -> dedupe and rank -> synthesize with citations.
- Every claim must link to a source URL in the evidence list.
- Run the prompt-injection check on every fetched page before the model sees it.

### 3.3 Knowledge agent (agentic RAG + GraphRAG)
- Custom FastMCP server (`mcp_servers/knowledge/`) exposing: `vector_search`, `graph_query`, `get_document`, `list_sources`.
- Stores: Qdrant (hybrid dense + keyword search), Neo4j (entities and relations).
- Embeddings: `bge-m3` locally (handles French, English, Arabic).
- Demo corpus: open documentation (MCP spec, A2A spec, framework docs) plus the project's own notes. No private or scraped personal data.
- Ingestion: parse -> section-aware chunking with overlap -> embed -> LLM entity and relation extraction -> write chunks to Qdrant and graph to Neo4j, chunks linked to entities.
- Query flow: classify (lookup / multi-hop / global) -> route (vector, graph+vector, community summaries) -> retrieve -> grade relevance -> rewrite query and retry (max 2) -> answer with chunk-level citations.

### 3.4 Action agent
- MCP tools: GitHub (read, create issue, comment, branch, PR) and Google Calendar (free slots, create and update event). Optional: Gmail drafts, filesystem write.
- Policy table classifies every tool as `read`, `write` or `destructive`. Read runs freely, write needs approval, destructive is blocked in v1.
- Flow: understand intent -> build action proposal -> LangGraph interrupt for approval (approve / edit / deny) -> execute -> verify result -> write action log.
- Use idempotency keys. Credentials have minimal scope. Never push to main; only branches and PRs.
- Stretch: code-fix workflow in a Docker sandbox with no network: read issue -> search repo -> run tests -> patch -> re-run tests -> show diff -> approval -> branch and PR.

### 3.5 Verifier
- Flow: extract claims -> match each to evidence -> label supported / unsupported / contradicted -> check the answer addresses the request -> return verdict and fixes.
- For action proposals, check that the proposal matches user intent (right repo, calendar, recipient).
- One repair loop allowed, then report uncertainty honestly.

## 4. Shared components

- Tool router (`router/`): index every MCP tool (name, description, example queries) in a vector collection. For each task retrieve top-k tools (k = 5-8) plus a small always-on core set. Expose a function used by all agents.
- Safety layer (`safety/`): permission levels per tool, approval gate, injection detector for tool outputs, credential scoping.
- Memory (`memory/`): LangGraph checkpointer for short-term; a Qdrant collection for long-term (preferences, past tasks).
- Observability: Langfuse tracing on every LLM and tool call, tagged with `task_id`, agent name and tool name.

## 5. Repository layout

Create exactly this structure with a short `README.md` or `__init__.py` placeholder in each folder.

```
agent-platform/
├── README.md
├── docker-compose.yml
├── .env.example
├── pyproject.toml
├── docs/
│   ├── architecture.md
│   ├── decisions/                 # short ADRs for key choices
│   └── demo-script.md
├── common/
│   ├── schemas.py                 # evidence, action proposal, task state (pydantic)
│   ├── llm.py                     # model-agnostic client (Mistral, Gemini, Ollama, Anthropic)
│   ├── a2a_client.py              # helpers to call other agents
│   └── tracing.py                 # Langfuse setup
├── orchestrator/
│   ├── graph.py                   # LangGraph supervisor
│   ├── planner.py
│   ├── api.py                     # FastAPI app
│   └── Dockerfile
├── agents/
│   ├── research/                  # A2A service + agent card
│   ├── knowledge/
│   ├── action/
│   └── verifier/
│       (each: server.py, graph.py, agent_card.json, Dockerfile)
├── mcp_servers/
│   ├── knowledge/                 # custom FastMCP server
│   └── config/                    # which off-the-shelf MCP servers to launch
├── knowledge/
│   ├── ingest/                    # parse, chunk, embed, extract graph
│   ├── corpus/                    # open docs used for the demo
│   └── schema/                    # Neo4j graph schema
├── router/
│   ├── index_tools.py
│   └── retrieve.py
├── safety/
│   ├── policy.yaml                # tool -> read|write|destructive
│   ├── approval.py
│   └── injection_check.py
├── memory/
├── sandbox/                       # stretch: Docker sandbox for code-fix
├── eval/
│   ├── tasks/                     # 40 benchmark tasks (yaml)
│   ├── configs/                   # variants to compare
│   ├── run_benchmark.py
│   └── results/
├── ui/                            # chat, approval cards, trace panel
└── tests/
```

## 6. Tech stack

- Python 3.11+, LangGraph, FastAPI, pydantic.
- MCP: official Python SDK or FastMCP. A2A: official A2A SDK.
- Qdrant, Neo4j Community, Langfuse (all self-hosted via Docker Compose).
- Embeddings: sentence-transformers (`bge-m3`).
- Evaluation: RAGAS or DeepEval plus custom task-success checks.
- UI: Streamlit first; Next.js only if time remains.
- LLM: configurable by environment variable. Default to a free-tier API during development, local Ollama as an option.

## 7. Build order

1. Scaffolding: folders, `pyproject.toml`, `docker-compose.yml` with Qdrant, Neo4j, Langfuse, `.env.example`, `common/`.
2. Orchestrator with one MCP tool, end to end, with tracing.
3. Knowledge pipeline: ingestion, custom MCP server, Knowledge agent (baseline RAG first, then graph, then agentic loop).
4. Tool router.
5. Action agent with approval gate (GitHub, Calendar).
6. Research agent.
7. Verifier.
8. Split agents into A2A services with agent cards.
9. Benchmark (40 tasks), results table, README update.
10. UI polish, demo script, 2-minute video.

## 8. Evaluation plan

- 40 tasks in `eval/tasks/`, each with a prompt, the allowed tools, and a pass/fail check.
- Configurations: (a) all tools in prompt vs routed tools; (b) single agent vs multi-agent; (c) naive RAG vs hybrid vs GraphRAG vs agentic GraphRAG.
- Metrics: task success rate, tool-selection accuracy, steps per task, latency, token cost, RAG faithfulness and context precision.
- Report honest numbers, including failures (for example, success rate of the code-fix workflow on seeded bugs).

## 9. Conventions for Claude Code

- Keep each service independently runnable and switch-off-able without breaking the others.
- Type everything with pydantic. No secrets in code; use `.env`.
- Every tool call and LLM call must be traced with `task_id`.
- Write tests for: tool router retrieval, approval gate, injection check, evidence schema.
- Prefer small commits per build step. Update `docs/architecture.md` when contracts change.
- Never execute generated code outside the sandbox. Never push to a main branch.

## 10. Definition of done

- `docker compose up` starts the full stack.
- Demo flow works: "Plan my week from my calendar, GitHub issues and notes", with an approval card for the writes.
- Benchmark results table is committed and shown at the top of this README.
- README includes an architecture diagram, setup steps and a 2-minute demo video link.
