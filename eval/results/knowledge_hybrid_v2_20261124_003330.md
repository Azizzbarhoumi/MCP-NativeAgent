# Knowledge eval: hybrid+v2

| hybrid+v2 | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 83% | 17% | 22.6 | 1716 |
| exact_match | 11 | 100% | 100% | 73% | 18% | 31.4 | 1723 |
| multi_hop | 8 | 88% | 50% | 50% | 38% | 32.2 | 1760 |
| all | 31 | 97% | 87% | 71% | 23% | 28.2 | 1730 |

## Failures

- **lo02** (lookup, partial, hit=True, coverage=1.0): How many jobs can a matrix generate at most per workflow run?
  - expected: 256 jobs per workflow run (GitHub-hosted and self-hosted).
  - got: The maximum number of jobs a matrix can generate per workflow run is **256** [1][3].
  - judge: Provides the correct number (256) but omits the detail that it applies to both GitHub‑hosted and self‑hosted runners.
  - retrieved: workflow-syntax.md, reuse-workflows.md, limits.md, run-job-variations.md, workflow-syntax.md
- **lo07** (lookup, partial, hit=True, coverage=1.0): What is the default delay before a Celery task is retried?
  - expected: 3 minutes (Task.default_retry_delay); it can be overridden with countdown.
  - got: The default delay before a Celery task is retried is **3 minutes** (180 seconds) [1][3]. This is set by the `Task.default_retry_delay` attribute.

Missing: The context does not specify whether this applies to all retry scenarios (e.g., worker loss) or only explicit retries via `Task.retry()`.
  - judge: Provides the default delay (3 minutes) but omits the reference fact that it can be overridden with countdown.
  - retrieved: tasks.rst, configuration.rst, tasks.rst, workers.rst, optimizing.rst
- **ex06** (exact_match, partial, hit=True, coverage=1.0): Which flag makes Ruff apply unsafe fixes too?
  - expected: --unsafe-fixes together with --fix (or the unsafe-fixes setting).
  - got: The flag that makes Ruff apply **unsafe fixes** is [`--unsafe-fixes`][1][2][3][4].

---
Missing: The exact command-line syntax (e.g., whether it must be combined with `--fix`) is implied but not explicitly repeated in all passages.
  - judge: Mentions the flag but omits that it must be used together with --fix (or the setting).
  - retrieved: linter.md, faq.md, features.md, linter.md, linter.md
- **ex10** (exact_match, partial, hit=True, coverage=1.0): Which exception does a Celery task catch to clean up before its hard time limit?
  - expected: celery.exceptions.SoftTimeLimitExceeded
  - got: The Celery task catches the **`SoftTimeLimitExceeded`** exception to clean up before its hard time limit is reached.

Supporting passages:
- [1] *"The ~@SoftTimeLimitExceeded exception will be raised when this is exceeded."*
- [2] *"this raises an exception the task can catch to clean up before the hard time limit kills it: ... `SoftTimeLimitExceeded`"*
- [3] *"When a *soft* time limit fires, a ~c
  - judge: Identifies the correct exception name but omits the full module path `celery.exceptions.SoftTimeLimitExceeded`.
  - retrieved: configuration.rst, workers.rst, tasks.rst, optimizing.rst, calling.rst
- **ex11** (exact_match, incorrect, hit=True, coverage=1.0): What Dockerfile RUN line caches pip downloads between builds?
  - expected: RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
  - got: The Dockerfile **does not explicitly use cache mounts** for `pip` downloads between builds in the provided context [1][3][5]. Instead, it relies on Docker’s **internal layer caching** for the `RUN pip install` step by copying `requirements.txt` first [1][3].

However, **cache mounts** (e.g., `--mount=type=cache,target=/root/.cache/pip`) are shown in [2][4] as a way to persist pip cache across buil
  - judge: Assistant claims no cache mount is used, contradicting the reference which specifies the exact RUN line with --mount=type=cache.
  - retrieved: docker.md, optimize.md, docker.md, optimize.md, docker.md
- **mu01** (multi_hop, partial, hit=True, coverage=0.5): A CI job runs pytest but finds no test files. What exit code does pytest return, and which workflow step setting lets the job still pass when that step fails?
  - expected: Exit code 5 (no tests were collected); set continue-on-error: true on the step.
  - got: The CI job returns **exit code 5** when no tests are collected [1].

To make the job still pass when no tests are collected, use the **`pytest-custom_exit_code` plugin** to customize the exit code [1].
  - judge: Correctly states exit code 5, but incorrectly suggests using a pytest plugin instead of setting continue-on-error: true.
  - retrieved: exit-codes.rst, reference.rst, writing_plugins.rst, capture-warnings.rst, ci.rst
- **mu03** (multi_hop, partial, hit=True, coverage=0.5): In a CI lint step, `ruff check` fails the job. What exit code caused it, and how do you make that step non-blocking in GitHub Actions without changing Ruff's exit code?
  - expected: ruff check exits 1 when violations are found; set continue-on-error: true on the step.
  - got: The step fails with an exit code of **`1`** (violations found) [1].

To make the step non-blocking in GitHub Actions **without changing Ruff’s exit code**, use the [`ruff-action`](https://github.com/astral-sh/ruff-action) with the `fail-on` option set to `false` in the workflow YAML, as it allows overriding the default pass-fail behavior [2].

*(Missing: The exact syntax for setting `fail-on: fals
  - judge: Correctly states exit code 1, but suggests using ruff-action fail-on false instead of the required continue-on-error: true.
  - retrieved: linter.md, integrations.md, linter.md, integrations.md, formatter.md
- **mu05** (multi_hop, incorrect, hit=False, coverage=0.0): To speed up a Python CI pipeline, how do you cache pip in the Dockerfile build, and after how long does GitHub evict unused Actions cache entries?
  - expected: Use RUN --mount=type=cache,target=/root/.cache/pip in the Dockerfile; GitHub removes cache entries not accessed in over 7 days.
  - got: To cache pip dependencies **in the Dockerfile build**, use the `type=cache` mount with the target path for pip’s cache (e.g., `/root/.cache/pip` or `/home/runner/.cache/pip` on Ubuntu runners). Example:
```Dockerfile
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
```
[4]

GitHub evicts unused Actions cache entries **after a certain period** (exact duration not
  - judge: Provides correct Dockerfile cache usage but fails to state the 7‑day eviction period, claiming the timeframe is unspecified.
  - retrieved: gha.md, cache.md, gha.md, python.md, migrate-from-gitlab-cicd.md
- **mu08** (multi_hop, partial, hit=True, coverage=0.5): Your FastAPI tests fail with an import error for httpx in CI. What must be installed, and which GitHub Actions debug variable gives more detailed step logs to confirm it?
  - expected: Install httpx (needed by TestClient); set ACTIONS_STEP_DEBUG to true.
  - got: The import error for `httpx` in FastAPI tests must be resolved by ensuring the `httpx` package is installed in your CI environment [1].

To confirm the issue and get detailed step logs, use the `--debug` buildkitd flag in the Docker Setup Buildx action, which enables BuildKit container logs [2]. The relevant debug variable is `buildkitd-flags: --debug`.
  - judge: Correctly mentions installing httpx, but gives the wrong GitHub Actions debug variable (should be ACTIONS_STEP_DEBUG, not buildkitd-flags).
  - retrieved: testing.md, configure-builder.md, first-steps.md, monitor-and-troubleshoot.md, index.md
