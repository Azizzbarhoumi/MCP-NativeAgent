# MCP-NativeAgent

A safety-first **Git and GitHub assistant for beginners**, built on MCP (Model Context
Protocol) tools, retrieval over the official Git and GitHub docs, and a reproducible
benchmark of broken repositories.

It inspects the real state of a local repo, explains the error in plain language, proposes
a fix as steps with a risk tier, and runs each step only after the user approves it. A
dangerous step needs an extra confirmation and gets a backup first.

> Status: research project in progress. The local git tool, the grader and the benchmark
> work. The tool is not yet wired to the Action agent (the next step).

## Results so far

Broken-repo benchmark, 8 scenarios, 3 runs each (24 answers), graded by a separate judge
model (Groq `openai/gpt-oss-120b`). The answer model is Mistral `mistral-small-latest`.

| Setup | Right diagnosis | Right fix | Unsafe suggestions | Destructive steps proposed |
|---|---|---|---|---|
| Repo inspection + docs + safety review | 75% | 75% | 1 / 24 | 1 |
| Same, without the safety review | 71% | 62% | 4 / 24 | 4 |

Grader validation (no LLM involved): for each of the 7 repo-state scenarios, the state check
**fails** on the broken repo, **passes** after a known-good fix, and **fails** after a
typical beginner mistake (7/7). 0 dangerous steps ran without a backup.

Knowledge pipeline (31-question set over developer docs, judged by Groq):
baseline 29%, improved prompt 74%, prompt + groundedness checker 77%.

## How it works

```
question + error ──> inspect repo (read-only MCP tools) ──> search git_docs (hybrid RAG)
        ──> LLM plan: cause, explanation, steps, warnings
        ──> risk tier per step, rated in the repo's state after the previous steps
        ──> safety review: a destructive step triggers one request for a safer plan
        ──> per-step approval ──> execute ──> new state + undo command
```

### The local git tool (`mcp_servers/git_local/`)

- **Read tools** (no approval): `repo_state`, `git_log`, `git_diff`, `git_reflog`,
  `git_show`, `rate_command`.
- **Write tools**: `list_operations`, `prepare_step`, `execute_step`. There is no raw-command
  tool: the model picks a typed operation (`switch`, `commit`, `revert`, `reset_hard`, ...)
  and fills validated parameters.
- **Risk tiers**:
  - `safe`: the read tools, which need no click.
  - `recoverable`: needs approval.
  - `dangerous` (reset --hard, force push, clean, rebase, branch -D, switching away from
    unsaved detached commits): needs approval plus a confirmation phrase. It shows exactly
    what would be lost and creates a backup ref first.
  - `blocked`: config changes, credentials, deleting the repo, anything outside the
    allowed folder.
- **Approval tokens** are HMAC-bound to the repo, the operation, the exact parameters and
  the current HEAD. Each one is single use and expires after 10 minutes. A changed command
  or a moved HEAD needs a new approval.
- **Hardening against hostile repos.** A repo's own config can make git run programs, so:
  - a repo whose `.git/config` sets hooks, fsmonitor, filters, textconv, merge drivers,
    credential helpers, aliases, includes or `core.worktree` is refused;
  - every call also overrides those settings, ignores system and global config, uses an
    allowlisted environment, has no shell, and has a timeout and output caps;
  - paths must stay inside the allowed root, with no symlinks on the way;
  - arguments that look like options or credentials are rejected.
- Every call is logged with its `task_id` in `logs/git_exec.jsonl` (git-ignored).

### Benchmark (`eval/git_scenarios/`)

Scripts build each broken repo from scratch, with identical commit ids on every machine:

- merge conflict
- rejected push
- detached HEAD
- commit on the wrong branch
- pushed bad commit
- committed `.env`
- missing upstream
- HTTPS password refused

Each scenario has an answer key, a **state check** run on the repo after the fix, and
**invariants** checked on every run (no credentials in remote URLs or config, and more
coming). The auth scenario can't be fixed locally, so it's graded with an answer rubric
(`rubrics/auth_https_password.yaml`).

### Knowledge (`knowledge/`)

- Qdrant with dense (bge-small via fastembed) and BM25 sparse vectors, fused with RRF.
- An optional query decomposition step.
- A groundedness and relevance checker that retries once and then refuses.
- Two collections:
  - `dev_docs`: GitHub Actions, Docker, FastAPI, Ruff, pytest and Celery docs.
  - `git_docs`: git's own documentation, GitHub's docs on git, authentication and pull
    requests, and Pro Git.
- A standalone graph viewer of the extracted entities (`knowledge/graph/`).
- Troubleshooting chains in the viewer for git_docs: repo state → error message → cause → command (with its risk tier) → concept → doc section, from a hand-written playbook (`knowledge/playbooks/git_errors.yaml`). Build with `python -m knowledge.graph.export --collection git_docs` and open `knowledge/graph/viewer/knowledge_graph.html`.

  ![Troubleshooting chains](docs/screenshots/graph_chains_all.png)

## Quickstart (Windows, PowerShell or cmd)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -e .
copy .env.example .env          &:: then add MISTRAL_API_KEY and GROQ_API_KEY in .env
docker compose up -d qdrant     &:: vector store
```

Build the docs collection (downloads only the folders it needs from the source repos):

```bat
python -m knowledge.acquire --collection git_docs
python -m knowledge.ingest --collection git_docs
python -m knowledge.ask "Why is my git push rejected?" --collection git_docs --check
```

Grader and benchmark:

```bat
python -m pytest -q                                   :: unit and security tests
python -m eval.git_scenarios                          :: build the broken repos
python -m eval.git_scenarios.check_fixes              :: validate the state checks (no LLM)
python -m eval.git_scenarios.rubric auth_https_password --demo
python -m eval.run_git_eval --runs 3                  :: assistant benchmark
python -m eval.run_git_eval --runs 3 --no-safety-retry
```

Inspect a repo by hand (read-only):

```bat
python -m mcp_servers.git_local eval\git_scenarios\build\merge_conflict\work --rate "git push --force"
```

## Local web UI

One command builds a broken scenario repo and opens it in your browser:

```bat
python -m git_assistant.webui --demo                  :: merge conflict
python -m git_assistant.webui --demo push_rejected    :: or any tuned scenario
python -m git_assistant.webui                         :: start empty, build repos from the page
```

The page address (with a random secret in it) and every approval code are printed in that
terminal. For each repo the page shows:
- the problem: what the user said, the command that failed and its real output;
- the repo state, read by the tool;
- the plain-language explanation: from the assistant (one LLM call; it uses your Groq budget),
  or from the scenario's answer key when you click "Show the answer key instead" (no LLM);
- the proposed fix, with a risk tier on every step. The tiers come from the one table in
  `mcp_servers/git_local/tiers.py`, which the tool also enforces.

Running a step goes through the tool's approval gate:
- **safe** steps run with one click;
- **careful** steps need the single-use approval code from the terminal. The code expires
  after 10 minutes, and 3 wrong tries cancel the step;
- **destructive** steps need the code **and** the typed phrase, and a backup is made first.

With `GIT_APPROVAL_UI=1`, an Approve button carries the code instead, as described in
`docs/approval_page.md`. This is off by default. After each step the repo is inspected again,
and "Did it work?" shows the scenario's state check plus the safety checks: no data loss, no
rewrite of shared history, backups, no leaked secret, and `git fsck`.

For now the UI only works on scenario repos it builds in a temporary workspace, and the git
tool is limited to that folder. The 6 held-out scenarios are not offered. The page listens on
127.0.0.1 only, checks the Host and Origin headers, and has no JavaScript.

| the problem and the repo | plan, tiers and approval | after the fix |
|---|---|---|
| ![problem](docs/screenshots/webui_problem_and_state.png) | ![plan](docs/screenshots/webui_plan_and_approval.png) | ![fixed](docs/screenshots/webui_fixed.png) |

The screenshots use the answer-key plan (no LLM), so they show the flow, not the assistant's
wording.

## Repository layout

```
common/            LLM client (Mistral, Groq), config, tracing (Langfuse)
knowledge/         acquisition, ingestion, retrieval, answering, checker, graph export
mcp_servers/       git_local (hardened local git tool); config/ holds the GitHub MCP env file (git-ignored)
git_assistant/     diagnose pipeline: inspect -> retrieve -> plan -> rate -> safety review;
                   webui.py + session.py: the local web UI
orchestrator/      LangGraph orchestrator using the read-only GitHub MCP server
eval/              knowledge eval, git scenarios, state checks, invariants, rubrics, results
tests/             chunking, RRF, metrics, redaction, git tool security, grader
docs/              architecture, decisions (ADRs), original spec
```

## Data and licenses

Third-party documentation is **downloaded locally and never committed**
(`knowledge/corpus/` is git-ignored). Attribution and licenses are recorded in
`knowledge/corpus_manifest.yaml`.

- Git documentation: GPL-2.0
- GitHub docs: CC BY 4.0
- Pro Git (Chacon and Straub): CC BY-NC-SA 3.0, **non-commercial**. Remove it before any
  commercial use.
- Developer docs (Docker, FastAPI, Ruff, pytest, Celery, GitHub Actions): see the manifest.

Benchmark data under `eval/` is never indexed into the knowledge base (enforced in code).

## Security notes

- Secrets live only in `.env` and `mcp_servers/config/github.env`, both git-ignored.
  `.env.example` lists the variable names.
- The GitHub MCP server runs read-only.
- The assistant never reads, writes or prints tokens or passwords. Authentication problems
  are explained, not fixed.

## Roadmap

1. Invariants on every run (no data loss, no force-push to shared branches, nothing changed
   outside the root, backups before dangerous steps).
2. Pushed-secret scenario and a held-out set of 8 to 10 variants that are never used for
   tuning.
3. End-to-end assistant runs that execute the plan and grade the final repo state, with
   separate tables for the tuned and held-out sets.
4. Wire the tool to the Action agent, with the approval token held by the agent code, never
   by the model.
