# Knowledge eval: baseline+v2

| baseline+v2 | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 100% | 0% | 28.1 | 1535 |
| exact_match | 11 | 100% | 100% | 82% | 18% | 30.5 | 1558 |
| multi_hop | 8 | 88% | 38% | 38% | 62% | 30.4 | 1639 |
| all | 31 | 97% | 84% | 77% | 23% | 29.6 | 1570 |

## Failures

- **ex06** (exact_match, partial, hit=True, coverage=1.0): Which flag makes Ruff apply unsafe fixes too?
  - expected: --unsafe-fixes together with --fix (or the unsafe-fixes setting).
  - got: The flag that makes Ruff apply unsafe fixes is `--unsafe-fixes` [1][2][3][4][5].
  - judge: Omits the required `--fix` or `unsafe-fixes` setting requirement from the reference.
  - retrieved: linter.md, faq.md, features.md, linter.md, linter.md
- **ex07** (exact_match, partial, hit=True, coverage=1.0): How do you suppress Ruff rule F841 on a single line?
  - expected: Add `# noqa: F841` at the end of the line.
  - got: To suppress Ruff rule **F841** on a single line, use one of these methods from the context:

1. **Line-level `ruff: ignore` comment** (applies to the next logical line):
   ```python
   # ruff: ignore[F841]
   x = 1  # This line will be ignored for F841
   ```

2. **Inline `noqa` comment** (applies to the current line):
   ```python
   x = 1  # noqa: F841
   ```

Missing: The context does not spec
  - judge: The assistant omits the key fact that `# noqa: F841` must be placed *at the end* of the line.
  - retrieved: linter.md, linter.md, linter.md, linter.md, formatter.md
- **mu01** (multi_hop, partial, hit=True, coverage=0.5): A CI job runs pytest but finds no test files. What exit code does pytest return, and which workflow step setting lets the job still pass when that step fails?
  - expected: Exit code 5 (no tests were collected); set continue-on-error: true on the step.
  - got: The CI job would receive **exit code 5** from pytest when no tests are collected [1].

To make the job pass despite this failure, you can use the **`pytest-custom_exit_code` plugin** to customize the exit code in scenarios where no tests are collected [1]. *(Missing: The exact plugin configuration or environment variable to enable this customization is not specified.)*
  - judge: Missing the key fact about using `continue-on-error: true` in the workflow step setting.
  - retrieved: exit-codes.rst, ci.rst, failures.rst, cache.rst, reference.rst
- **mu02** (multi_hop, partial, hit=False, coverage=0.0): A pytest suite in GitHub Actions runs for 7 hours on a GitHub-hosted runner. What happens, and what is the default job timeout?
  - expected: GitHub-hosted jobs are limited to 6 hours of execution time, so the job is terminated and fails; the default timeout-minutes is 360.
  - got: The retrieved documents do not contain the answer to the **default job timeout** for GitHub-hosted runners.

However, they confirm that:
- If a workflow run is **queued but not processed within 45 minutes**, it is discarded [2].
- GitHub-hosted runners execute jobs in **ephemeral, isolated virtual machines** [4][5], but this does not specify a timeout for active job execution.

Missing: The defaul
  - judge: The assistant correctly identifies the 6-hour execution limit but omits the default timeout-minutes value (360) from the reference.
  - retrieved: github-hosted-runners.md, github-hosted-runners.md, actions-runner-controller.md, secure-use.md, understand-github-actions.md
- **mu03** (multi_hop, partial, hit=True, coverage=0.5): In a CI lint step, `ruff check` fails the job. What exit code caused it, and how do you make that step non-blocking in GitHub Actions without changing Ruff's exit code?
  - expected: ruff check exits 1 when violations are found; set continue-on-error: true on the step.
  - got: The exit code that caused the CI lint step to fail is **`1`** (when violations were found) [1].

To make the step non-blocking in GitHub Actions **without changing Ruff's exit code**, you can use the `ruff-action` with the `args` parameter to pass `--exit-zero` to `ruff check` [1][4]. For example:

```yaml
- uses: astral-sh/ruff-action@v3
  with:
    args: check --exit-zero
```

*(Missing: The exa
  - judge: The assistant correctly identifies the exit code (1) but does not mention the `continue-on-error: true` solution from the reference, instead suggesting an alternative (`--exit-zero`) that is not mentioned in the reference.
  - retrieved: linter.md, integrations.md, linter.md, integrations.md, linter.md
- **mu04** (multi_hop, partial, hit=True, coverage=1.0): FastAPI docs suggest a bigger tool for heavy background work. Which tool, and what Celery settings make a task acknowledged only after it runs with prefetching of one task?
  - expected: Celery; task_acks_late = True and worker_prefetch_multiplier = 1.
  - got: The retrieved documents suggest using **[Celery]** for heavy background computation [1].

To make a task acknowledged only after it runs with prefetching of one task, use these **Celery settings**:
- `task_acks_late = True`
- `worker_prefetch_multiplier = 1` [5]

*(Missing: No passage explains why Celery is preferred over FastAPI's `BackgroundTasks` for heavy tasks.)*
  - judge: The assistant omits the explanation of why Celery is preferred over FastAPI's BackgroundTasks for heavy tasks, which is a key fact in the reference.
  - retrieved: background-tasks.md, background.md, background-tasks.md, monitoring.rst, optimizing.rst
- **mu08** (multi_hop, partial, hit=True, coverage=0.5): Your FastAPI tests fail with an import error for httpx in CI. What must be installed, and which GitHub Actions debug variable gives more detailed step logs to confirm it?
  - expected: Install httpx (needed by TestClient); set ACTIONS_STEP_DEBUG to true.
  - got: The `httpx` package must be installed as part of the `standard` dependencies for FastAPI [5].

The GitHub Actions debug variable to enable more detailed step logs is **`GITHUB_DEBUG`** (though it is not explicitly mentioned in the context, the closest related command for debugging in CI would be setting `GITHUB_TOKEN` or enabling CI logs via environment variables—**this part is missing** from the 
  - judge: The assistant correctly identifies the need to install `httpx` but incorrectly names the debug variable (should be `ACTIONS_STEP_DEBUG` instead of `GITHUB_DEBUG` or `GITHUB_TOKEN`).
  - retrieved: testing.md, first-steps.md, index.md, first-steps.md, index.md
