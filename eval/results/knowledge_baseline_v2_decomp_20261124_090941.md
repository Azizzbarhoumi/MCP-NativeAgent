# Knowledge eval: baseline+v2_decomp

| baseline+v2_decomp | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 92% | 8% | 26.0 | 2190 |
| exact_match | 11 | 100% | 100% | 82% | 9% | 30.5 | 2055 |
| multi_hop | 8 | 100% | 75% | 50% | 38% | 29.4 | 2549 |
| all | 31 | 100% | 94% | 77% | 16% | 28.5 | 2235 |

Decomposer: split 5/8 multi_hop questions, 4/23 others (should be 0)

Wrong answers delivered (incorrect and not a refusal): 1/31

## Failures

- **lo07** (lookup, partial, hit=True, coverage=1.0): What is the default delay before a Celery task is retried?
  - expected: 3 minutes (Task.default_retry_delay); it can be overridden with countdown.
  - got: The default delay before a Celery task is retried is **3 minutes** (180 seconds) [1][2].

Missing: The context does not specify whether this applies to all retry scenarios (e.g., worker loss) or only to explicit `retry()` calls.
  - judge: Provides the default delay but omits the reference fact that it can be overridden with countdown.
  - retrieved: tasks.rst, configuration.rst, application.rst, configuration.rst, tasks.rst
- **ex06** (exact_match, partial, hit=True, coverage=1.0): Which flag makes Ruff apply unsafe fixes too?
  - expected: --unsafe-fixes together with --fix (or the unsafe-fixes setting).
  - got: The flag that makes Ruff apply **unsafe fixes** is [`--unsafe-fixes`](settings.md#unsafe-fixes) [1][2][3][4][5].

Missing: The equivalent **configuration setting** to enable unsafe fixes.
  - judge: Provides the flag but omits the required --fix usage and the configuration setting
  - retrieved: linter.md, faq.md, features.md, linter.md, linter.md
- **ex11** (exact_match, incorrect, hit=True, coverage=1.0): What Dockerfile RUN line caches pip downloads between builds?
  - expected: RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
  - got: The retrieved documents do not contain the answer.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: best-practices.md, optimize.md, python.md, cache.md, migrate-from-circleci.md, invalidation.md, docker.md, migrate-from-gitlab-cicd.md
- **mu02** (multi_hop, incorrect, hit=True, coverage=0.5): A pytest suite in GitHub Actions runs for 7 hours on a GitHub-hosted runner. What happens, and what is the default job timeout?
  - expected: GitHub-hosted jobs are limited to 6 hours of execution time, so the job is terminated and fails; the default timeout-minutes is 360.
  - got: The job is **automatically canceled** after **7 hours** because the default job timeout for GitHub-hosted runners is **360 minutes (6 hours)** [6].

**Missing:** No passage specifies what happens if the job exceeds the timeout (e.g., whether it fails silently, logs are preserved, or cleanup occurs).
  - judge: Assistant states the job is canceled after 7 hours, contradicting the reference that it ends after the 6‑hour (360 min) limit.
  - retrieved: github-hosted-runners.md, usage.rst, migrate-from-travis-ci.md, github-hosted-runners.md, output.rst, workflow-syntax.md, actions-runner-controller.md, index.rst
- **mu03** (multi_hop, partial, hit=True, coverage=0.5): In a CI lint step, `ruff check` fails the job. What exit code caused it, and how do you make that step non-blocking in GitHub Actions without changing Ruff's exit code?
  - expected: ruff check exits 1 when violations are found; set continue-on-error: true on the step.
  - got: The exit code that causes `ruff check` to fail the job is **`1`** (when violations are found) [1].

To make the step non-blocking in GitHub Actions **without changing Ruff's exit code**, you can use the [`continue-on-error`](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idcontinue-on-error) keyword in the job definition:

```yaml
jobs:
  ruff:
    runs-
  - judge: Correctly identifies exit code 1 and using continue-on-error, but suggests setting it on the job rather than on the step as the reference specifies.
  - retrieved: linter.md, migrate-from-travis-ci.md, integrations.md, linter.md, migrate-from-travis-ci.md, linter.md, migration.md, migrate-from-travis-ci.md
- **mu05** (multi_hop, partial, hit=True, coverage=1.0): To speed up a Python CI pipeline, how do you cache pip in the Dockerfile build, and after how long does GitHub evict unused Actions cache entries?
  - expected: Use RUN --mount=type=cache,target=/root/.cache/pip in the Dockerfile; GitHub removes cache entries not accessed in over 7 days.
  - got: To cache pip dependencies in a **Dockerfile**, use the `RUN` instruction with a **cache mount** targeting the pip cache directory (e.g., `/root/.cache/pip` or `/root/.local/share/pip`):

```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
```
[5]

For **GitHub Actions**, the eviction policy removes stale cache entries after a certain period, but the e
  - judge: Correct Dockerfile cache syntax, but fails to state the 7‑day eviction period, claiming the duration is unspecified.
  - retrieved: migrate-from-gitlab-cicd.md, python.md, gha.md, cache.md, optimize.md, dependency-caching.md, invalidation.md, docker.md
- **mu06** (multi_hop, partial, hit=True, coverage=1.0): A CI run fails because conftest.py cannot be imported. What pytest exit code does that produce, and which command-line flag controls how pytest imports test modules and conftest files?
  - expected: Exit code 4; the --import-mode flag (default prepend).
  - got: The CI run fails because `conftest.py` cannot be imported, which produces **exit code 4** [2].

The command-line flag that controls how pytest imports test modules and `conftest.py` files is **not explicitly mentioned in the provided context** for this specific behavior. The closest related flag is `--trace-config`, which traces considerations of `conftest.py` files [5], but it does not directly c
  - judge: Correct exit code but fails to identify the --import-mode flag and gives an incorrect alternative.
  - retrieved: writing_plugins.rst, exit-codes.rst, pythonpath.rst, pythonpath.rst, reference.rst
