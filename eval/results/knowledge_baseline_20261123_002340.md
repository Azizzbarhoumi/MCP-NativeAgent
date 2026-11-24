# Knowledge eval: baseline

| baseline | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 67% | 0% | 28.1 | 1448 |
| exact_match | 11 | 100% | 100% | 18% | 9% | 29.9 | 1478 |
| multi_hop | 8 | 88% | 38% | 12% | 0% | 30.2 | 1520 |
| all | 31 | 97% | 84% | 35% | 3% | 29.3 | 1477 |

## Failures

- **lo02** (lookup, incorrect, hit=True, coverage=1.0): How many jobs can a matrix generate at most per workflow run?
  - expected: 256 jobs per workflow run (GitHub-hosted and self-hosted).
  - got: The retrieved documents do not contain the answer.

The context mentions a **maximum of 256 jobs per workflow run** [1], but does not explicitly state it as the answer to the question.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: workflow-syntax.md, run-job-variations.md, run-job-variations.md, reuse-workflows.md, workflow-syntax.md
- **lo05** (lookup, incorrect, hit=True, coverage=1.0): Where should a pytest fixture be placed so tests in multiple test modules of a directory can use it?
  - expected: In a conftest.py file in that directory.
  - got: The retrieved documents do not contain the answer.

The context does not explicitly state the *specific file* (e.g., `conftest.py` vs. test module) where a fixture should be placed to ensure *all* tests in a directory can access it—only that `conftest.py` files enable sharing across files within their directory.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: fixtures.rst, fixtures.rst, unittest.rst, fixtures.rst, unittest.rst
- **lo09** (lookup, incorrect, hit=True, coverage=1.0): What do you need to install before using FastAPI's TestClient?
  - expected: httpx.
  - got: The retrieved documents do not contain the answer.

The context does not specify any prerequisite installation for `TestClient` beyond FastAPI itself.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: testing.md, testclient.md, testing.md, index.md, index.md
- **lo12** (lookup, incorrect, hit=True, coverage=1.0): Can the retention-days of an uploaded artifact exceed the repository's retention limit?
  - expected: No, retention-days cannot exceed the limit set by the repository, organization, or enterprise.
  - got: The retrieved documents do not contain the answer.

The context does not specify whether the `retention-days` value for an artifact can exceed the repository's retention limit, only that it "cannot exceed the retention limit set by the repository, organization, or enterprise" [1].
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: store-and-share-data.md, limits.md, dependency-caching.md, dependency-caching.md, dependency-caching.md
- **ex01** (exact_match, incorrect, hit=True, coverage=1.0): What does pytest exit code 4 mean?
  - expected: pytest command line usage error, including a plugin that cannot be found or a conftest.py that fails to import.
  - got: The retrieved documents do not contain the answer.

The context does not explicitly define the meaning of exit code 4 in plain text—only that it is listed among the seven possible exit codes.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: exit-codes.rst, reference.rst, reference.rst, output.rst, reference.rst
- **ex04** (exact_match, incorrect, hit=True, coverage=1.0): What exit code does `ruff check` return when violations were found?
  - expected: 1.
  - got: The retrieved documents do not contain the answer.

The context does not specify the exact exit code for `ruff check` when violations are found *without* any special flags.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: linter.md, formatter.md, linter.md, linter.md, faq.md
- **ex05** (exact_match, incorrect, hit=True, coverage=1.0): What exit code does `ruff format --check` return if one or more files would be reformatted?
  - expected: 1.
  - got: The retrieved documents do not contain the answer.

The context does not specify the exact exit code for `ruff format --check` when one or more files would be reformatted, only that it exits with a non-zero status code upon detecting unformatted files.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: formatter.md, linter.md, linter.md, formatter.md, configuration.md
- **ex06** (exact_match, partial, hit=True, coverage=1.0): Which flag makes Ruff apply unsafe fixes too?
  - expected: --unsafe-fixes together with --fix (or the unsafe-fixes setting).
  - got: The flag that makes Ruff apply unsafe fixes is [`--unsafe-fixes`][1][2][3][4].
  - judge: Omits the required --fix (or unsafe-fixes setting) requirement from the reference.
  - retrieved: linter.md, faq.md, features.md, linter.md, linter.md
- **ex07** (exact_match, incorrect, hit=True, coverage=1.0): How do you suppress Ruff rule F841 on a single line?
  - expected: Add `# noqa: F841` at the end of the line.
  - got: The retrieved documents do not contain the answer.

The missing information is the exact syntax for suppressing **only** the Ruff rule `F841` on a single line using **line-level suppression comments**.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: linter.md, linter.md, linter.md, linter.md, formatter.md
- **ex08** (exact_match, incorrect, hit=True, coverage=1.0): Which pytest command stops the run after two failures?
  - expected: pytest --maxfail=2
  - got: The retrieved documents do not contain the answer.

The missing information is the specific command for stopping after two failures in the context of rerunning failures.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: failures.rst, cache.rst, cache.rst, cache.rst, cache.rst
- **ex09** (exact_match, incorrect, hit=True, coverage=1.0): Which pytest command lists the 10 slowest tests longer than 1 second?
  - expected: pytest --durations=10 --durations-min=1.0
  - got: The retrieved documents do not contain the answer.

The exact command is shown in passage [1], but the question asks for the command to list the slowest tests, not just the duration filter.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: usage.rst, usage.rst, reference.rst, reference.rst, mark.rst
- **ex10** (exact_match, incorrect, hit=True, coverage=1.0): Which exception does a Celery task catch to clean up before its hard time limit?
  - expected: celery.exceptions.SoftTimeLimitExceeded
  - got: The retrieved documents do not contain the answer.

The missing information is the **exact exception name** for the cleanup step before the hard time limit is enforced.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: configuration.rst, tasks.rst, workers.rst, calling.rst, tasks.rst
- **ex11** (exact_match, incorrect, hit=True, coverage=1.0): What Dockerfile RUN line caches pip downloads between builds?
  - expected: RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
  - got: The retrieved documents do not contain the answer.

The context does not explicitly mention which `RUN` line caches pip downloads between builds, but it does discuss caching for pip in [2] and [4].
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: best-practices.md, optimize.md, invalidation.md, docker.md, docker.md
- **mu01** (multi_hop, incorrect, hit=True, coverage=0.5): A CI job runs pytest but finds no test files. What exit code does pytest return, and which workflow step setting lets the job still pass when that step fails?
  - expected: Exit code 5 (no tests were collected); set continue-on-error: true on the step.
  - got: The retrieved documents do not contain the answer.

The missing information is the specific **workflow step setting** (e.g., CI/CD tool configuration) that allows bypassing a failure for exit code **5** (no tests collected).
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: exit-codes.rst, ci.rst, failures.rst, cache.rst, reference.rst
- **mu02** (multi_hop, incorrect, hit=False, coverage=0.0): A pytest suite in GitHub Actions runs for 7 hours on a GitHub-hosted runner. What happens, and what is the default job timeout?
  - expected: GitHub-hosted jobs are limited to 6 hours of execution time, so the job is terminated and fails; the default timeout-minutes is 360.
  - got: The retrieved documents do not contain the answer.

The context does not specify a default job timeout for GitHub-hosted runners or what happens if a job exceeds it.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: github-hosted-runners.md, github-hosted-runners.md, actions-runner-controller.md, secure-use.md, understand-github-actions.md
- **mu03** (multi_hop, incorrect, hit=True, coverage=0.5): In a CI lint step, `ruff check` fails the job. What exit code caused it, and how do you make that step non-blocking in GitHub Actions without changing Ruff's exit code?
  - expected: ruff check exits 1 when violations are found; set continue-on-error: true on the step.
  - got: The retrieved documents do not contain the answer.

The context does not explain how to make a failing `ruff check` step non-blocking in GitHub Actions without altering Ruff's exit code behavior.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: linter.md, integrations.md, linter.md, integrations.md, linter.md
- **mu04** (multi_hop, incorrect, hit=True, coverage=1.0): FastAPI docs suggest a bigger tool for heavy background work. Which tool, and what Celery settings make a task acknowledged only after it runs with prefetching of one task?
  - expected: Celery; task_acks_late = True and worker_prefetch_multiplier = 1.
  - got: The retrieved documents do not contain the answer.

The context does not specify the exact Celery settings for late acknowledgment with prefetching of one task.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: background-tasks.md, background.md, background-tasks.md, monitoring.rst, optimizing.rst
- **mu05** (multi_hop, incorrect, hit=True, coverage=0.5): To speed up a Python CI pipeline, how do you cache pip in the Dockerfile build, and after how long does GitHub evict unused Actions cache entries?
  - expected: Use RUN --mount=type=cache,target=/root/.cache/pip in the Dockerfile; GitHub removes cache entries not accessed in over 7 days.
  - got: The retrieved documents do not contain the answer.

The context lacks specific details on caching `pip` in a Python Dockerfile and the exact timeframe for GitHub’s cache eviction policy.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: migrate-from-gitlab-cicd.md, cache.md, invalidation.md, gha.md, optimize.md
- **mu07** (multi_hop, incorrect, hit=True, coverage=1.0): A Celery task fails on a flaky service in CI. Which argument to self.retry() makes it wait 60 seconds before retrying, and how many retries does a task get by default?
  - expected: countdown=60, e.g. raise self.retry(exc=exc, countdown=60); the default max_retries is 3.
  - got: The retrieved documents do not contain the answer.

The context does not specify how to set a 60-second delay in `self.retry()`—only that `countdown` can override the default delay (e.g., `countdown=60`). It also does not clarify the default retry count for CI failures.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: configuration.rst, calling.rst, tasks.rst, tasks.rst, tasks.rst
- **mu08** (multi_hop, incorrect, hit=True, coverage=0.5): Your FastAPI tests fail with an import error for httpx in CI. What must be installed, and which GitHub Actions debug variable gives more detailed step logs to confirm it?
  - expected: Install httpx (needed by TestClient); set ACTIONS_STEP_DEBUG to true.
  - got: The retrieved documents do not contain the answer.

The context does not mention CI environments, GitHub Actions, or specific import errors for `httpx` in CI.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: testing.md, first-steps.md, index.md, first-steps.md, index.md
