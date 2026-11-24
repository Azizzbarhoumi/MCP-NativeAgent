# Knowledge eval: baseline+v2_check

| baseline+v2_check | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 100% | 0% | 23.2 | 3346 |
| exact_match | 11 | 100% | 100% | 82% | 9% | 30.0 | 3344 |
| multi_hop | 8 | 100% | 50% | 38% | 50% | 35.9 | 5892 |
| all | 31 | 100% | 87% | 77% | 16% | 28.9 | 4002 |

Checker: retried 3/31, refused 1, correct after retry 1

Wrong answers delivered (incorrect and not a refusal): 1/31

## Failures

- **ex06** (exact_match, partial, hit=True, coverage=1.0): Which flag makes Ruff apply unsafe fixes too?
  - expected: --unsafe-fixes together with --fix (or the unsafe-fixes setting).
  - got: The flag that makes Ruff apply **unsafe fixes** is [`--unsafe-fixes`](settings.md#unsafe-fixes) [1][2][3][4][5].

Missing: The equivalent configuration setting in a config file is not explicitly named in the question, but it is referenced in the context.
  - judge: Only states the flag name, omitting that it must be used with --fix or via the unsafe-fixes setting.
  - retrieved: linter.md, faq.md, features.md, linter.md, linter.md
- **ex11** (exact_match, incorrect, hit=True, coverage=1.0): What Dockerfile RUN line caches pip downloads between builds?
  - expected: RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
  - got: The Dockerfile `RUN` line that caches pip downloads between builds is:

```dockerfile
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt
```
[4]

**Missing:** The context does not explain *how* or *why* this cache is preserved between builds (e.g., cache mount options or layer invalidation rules).
  - judge: The answer omits the required cache mount and uses a flag that disables caching, contradicting the reference.
  - retrieved: best-practices.md, optimize.md, invalidation.md, docker.md, docker.md
- **mu01** (multi_hop, partial, hit=True, coverage=0.5): A CI job runs pytest but finds no test files. What exit code does pytest return, and which workflow step setting lets the job still pass when that step fails?
  - expected: Exit code 5 (no tests were collected); set continue-on-error: true on the step.
  - got: The CI job would receive **exit code 5** from pytest when no tests are collected [1].

To make the job pass despite this failure, the workflow step could use the **`pytest-custom_exit_code` plugin** to customize the exit code for scenarios where no tests are collected [1].
  - judge: Correctly states exit code 5, but suggests a plugin instead of the correct 'continue-on-error: true' setting.
  - retrieved: exit-codes.rst, ci.rst, failures.rst, cache.rst, reference.rst
- **mu03** (multi_hop, incorrect, hit=True, coverage=0.5): In a CI lint step, `ruff check` fails the job. What exit code caused it, and how do you make that step non-blocking in GitHub Actions without changing Ruff's exit code?
  - expected: ruff check exits 1 when violations are found; set continue-on-error: true on the step.
  - got: The retrieved documents do not contain the answer. The draft answer contained claims the passages do not support: Claim that ruff-action allows overriding the failure condition via the args parameter; Claim that passing --exit-zero through ruff-action's args forces a success exit code (0) regardless of violations; Claim that ruff-action can make the step non-blocking without changing Ruff's exit c
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: linter.md, integrations.md, linter.md, integrations.md, linter.md, integrations.md, formatter.md
- **mu05** (multi_hop, partial, hit=True, coverage=0.5): To speed up a Python CI pipeline, how do you cache pip in the Dockerfile build, and after how long does GitHub evict unused Actions cache entries?
  - expected: Use RUN --mount=type=cache,target=/root/.cache/pip in the Dockerfile; GitHub removes cache entries not accessed in over 7 days.
  - got: To cache `pip` in a Dockerfile build, use a **cache mount** with the `--mount=type=cache` flag targeting the pip cache directory (e.g., `/root/.cache/pip` or `~/.cache/pip`). Example:

```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
```

---
The retrieved documents do not specify the exact eviction timeframe for GitHub Actions cache entries, but [
  - judge: Provides the correct cache mount syntax but fails to state the 7‑day eviction period and suggests the timeframe is unspecified.
  - retrieved: migrate-from-gitlab-cicd.md, cache.md, invalidation.md, gha.md, optimize.md
- **mu06** (multi_hop, partial, hit=True, coverage=1.0): A CI run fails because conftest.py cannot be imported. What pytest exit code does that produce, and which command-line flag controls how pytest imports test modules and conftest files?
  - expected: Exit code 4; the --import-mode flag (default prepend).
  - got: The CI run fails due to a `conftest.py` import error, which produces **exit code 4** [2].

The command-line flag that controls how pytest imports test modules and `conftest.py` files is **not explicitly mentioned in the provided passages**.

*(Missing: No passage directly names a flag for import modes; only [3][4] describe import behavior under different directory layouts.)*
  - judge: Correctly states exit code 4 but fails to identify the --import-mode flag.
  - retrieved: writing_plugins.rst, exit-codes.rst, pythonpath.rst, pythonpath.rst, reference.rst
- **mu08** (multi_hop, partial, hit=True, coverage=1.0): Your FastAPI tests fail with an import error for httpx in CI. What must be installed, and which GitHub Actions debug variable gives more detailed step logs to confirm it?
  - expected: Install httpx (needed by TestClient); set ACTIONS_STEP_DEBUG to true.
  - got: The missing dependency for FastAPI tests in CI is **`httpx`** (or `httpx2`), as referenced in the `standard` dependencies group for testing via `TestClient` [5].

To enable more detailed step logs in GitHub Actions, use the secret or environment variable **`ACTIONS_STEP_DEBUG`** set to `true` [6][9].

---
**What is still missing:**
No passage explicitly states that `httpx` must be installed in CI 
  - judge: Includes the correct facts but adds an unsupported claim about "httpx2" which is not in the reference.
  - retrieved: testing.md, first-steps.md, index.md, first-steps.md, index.md, enable-debug-logging.md, troubleshoot-workflows.md, use-workflow-run-logs.md, workflow-commands.md, variables.md
