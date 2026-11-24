# Knowledge eval: baseline+v2

| baseline+v2 | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| multi_hop | 4 | 100% | 0% | 0% | 100% | 13.0 | 2280 |
| all | 4 | 100% | 0% | 0% | 100% | 13.0 | 2280 |

Wrong answers delivered (incorrect and not a refusal): 0/4

## Failures

- **mu01** (multi_hop, partial, hit=True, coverage=0.5): A CI job runs pytest but finds no test files. What exit code does pytest return, and which workflow step setting lets the job still pass when that step fails?
  - expected: Exit code 5 (no tests were collected); set continue-on-error: true on the step.
  - got: The CI job would receive **exit code 5** from pytest when no tests are collected [1].

To make the CI job pass despite this failure, you can use the **pytest-custom_exit_code** plugin to customize the exit code for scenarios where no tests are collected [1].
  - judge: Correctly states exit code 5, but gives the wrong method for allowing the job to pass, contradicting the reference.
  - retrieved: exit-codes.rst, ci.rst, failures.rst, cache.rst, reference.rst, cache.rst, output.rst, getting-started.rst
- **mu03** (multi_hop, partial, hit=True, coverage=0.5): In a CI lint step, `ruff check` fails the job. What exit code caused it, and how do you make that step non-blocking in GitHub Actions without changing Ruff's exit code?
  - expected: ruff check exits 1 when violations are found; set continue-on-error: true on the step.
  - got: The step fails with an exit code of **`1`** [1].

To make the step non-blocking in GitHub Actions **without changing Ruff's exit code**, you can use the [`ruff-action`](https://github.com/astral-sh/ruff-action) and configure it to **not fail the job** by leveraging its default behavior as a "pass-fail test" (which already runs as a non-blocking step by default) or by explicitly setting the `args` 
  - judge: Correctly states exit code 1, but does not mention using continue-on-error: true and suggests alternative methods not in the reference.
  - retrieved: linter.md, integrations.md, linter.md, integrations.md, linter.md, configuration.md, settings.md, integrations.md
- **mu05** (multi_hop, partial, hit=True, coverage=0.5): To speed up a Python CI pipeline, how do you cache pip in the Dockerfile build, and after how long does GitHub evict unused Actions cache entries?
  - expected: Use RUN --mount=type=cache,target=/root/.cache/pip in the Dockerfile; GitHub removes cache entries not accessed in over 7 days.
  - got: To cache `pip` in a Dockerfile build, use a **cache mount** with the `--mount=type=cache` flag targeting the pip cache directory (e.g., `~/.cache/pip` or `/root/.cache/pip`). For example:

```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
```

---
The retrieved documents do not specify the exact eviction timeframe for GitHub Actions cache entries, b
  - judge: Correctly mentions the cache mount, but omits and contradicts the reference's 7‑day eviction period.
  - retrieved: migrate-from-gitlab-cicd.md, cache.md, invalidation.md, gha.md, optimize.md, optimize.md, migrate-from-circleci.md, cache.md
- **mu08** (multi_hop, partial, hit=True, coverage=0.5): Your FastAPI tests fail with an import error for httpx in CI. What must be installed, and which GitHub Actions debug variable gives more detailed step logs to confirm it?
  - expected: Install httpx (needed by TestClient); set ACTIONS_STEP_DEBUG to true.
  - got: The `httpx` package must be installed as part of the `fastapi[standard]` group of optional dependencies [5].

The GitHub Actions debug variable to enable more detailed step logs is **`GITHUB_ACTIONS_DEBUG`** (though not explicitly named in the context, the closest relevant debug-related variable in CI environments is typically `GITHUB_ACTIONS_DEBUG` or `CI_DEBUG_TRACE`). However, the context does 
  - judge: Correctly states httpx must be installed, but gives the wrong GitHub Actions debug variable (should be ACTIONS_STEP_DEBUG).
  - retrieved: testing.md, first-steps.md, index.md, first-steps.md, index.md, opentelemetry.md, first-steps.md, concepts.md
