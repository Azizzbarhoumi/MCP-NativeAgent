# MCP-NativeAgent: plan

## Architecture
- **5 agents** (each its own A2A service with an agent card): Orchestrator (LangGraph supervisor), Research (web search + fetch), Knowledge (agentic RAG + GraphRAG over Qdrant + Neo4j), Action (GitHub/Calendar, writes behind approval), Verifier (claims vs evidence).
- **Shared components**: Tool router (top 5-8 tools per task from a vector index), Safety layer (policy, approval gate, injection check), Memory (checkpointer + Qdrant), Observability (Langfuse, everything tagged with `task_id`).
- **Contracts**: shared evidence format `{answer, evidence[], confidence}` and typed action proposals `{tool, arguments, risk, preview, idempotency_key}`.
- **Infra**: Docker Compose with Qdrant, Neo4j, Langfuse; LLM chosen by env var.

## Folder structure and placeholders (`agent-platform/`)
| Path | Purpose |
|---|---|
| README.md | Copy of the project README |
| docker-compose.yml | Infra + services (empty stub) |
| .env.example | All env var names, no values |
| pyproject.toml | Project metadata; deps listed as TODO |
| docs/architecture.md, decisions/, demo-script.md | Architecture doc, ADRs, demo script |
| common/schemas.py | Evidence, ActionProposal, TaskState models |
| common/llm.py | Model-agnostic LLM client |
| common/a2a_client.py | Call other agents over A2A |
| common/tracing.py | Langfuse setup |
| orchestrator/graph.py, planner.py, api.py, Dockerfile | Supervisor graph, step planner, FastAPI app |
| agents/{research,knowledge,action,verifier}/server.py, graph.py, agent_card.json, Dockerfile | A2A service, agent flow, card, container |
| mcp_servers/knowledge/server.py | FastMCP: vector_search, graph_query, get_document, list_sources |
| mcp_servers/config/ | Off-the-shelf MCP server config |
| knowledge/ingest/parse.py, chunk.py, embed.py, extract_graph.py | Ingestion pipeline stages |
| knowledge/corpus/, schema/ | Demo docs, Neo4j schema |
| router/index_tools.py, retrieve.py | Tool indexing and top-k retrieval |
| safety/policy.yaml, approval.py, injection_check.py | Risk table, approval gate, injection detector |
| memory/ | Short and long-term memory |
| sandbox/ | Stretch code-fix sandbox |
| eval/tasks/, configs/, run_benchmark.py, results/ | Benchmark tasks, variants, runner, results |
| ui/ | Streamlit chat + approval cards |
| tests/test_router.py, test_approval.py, test_injection_check.py, test_schemas.py | Required tests |

Small additions beyond the README layout: `__init__.py` in Python packages, the four ingestion stage files, and the four test stubs.

## Build order (next)
1. Fill scaffolding (deps, compose with Qdrant/Neo4j/Langfuse, common/) 2. Orchestrator + one MCP tool + tracing 3. Knowledge pipeline 4. Router 5. Action + approval 6. Research 7. Verifier 8. Split into A2A services 9. Benchmark 10. UI + demo.
