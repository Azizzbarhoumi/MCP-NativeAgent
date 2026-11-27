# Knowledge eval: baseline+v2_check

Answer model: openrouter/openai/gpt-oss-20b, judge: groq

| baseline+v2_check | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 92% | 8% | 6.6 | 3624 |
| exact_match | 11 | 100% | 100% | 91% | 0% | 7.7 | 3682 |
| multi_hop | 8 | 88% | 75% | 62% | 0% | 11.1 | 6629 |
| all | 31 | 97% | 94% | 84% | 3% | 8.2 | 4420 |

Checker: retried 4/31, refused 1, correct after retry 3

Wrong answers delivered (incorrect and not a refusal): 1/31

## Failures

- **lo07** (lookup, partial, hit=True, coverage=1.0): What is the default delay before a Celery task is retried?
  - expected: 3 minutes (Task.default_retry_delay); it can be overridden with countdown.
  - got: The default retry delay for a Celery task is **3 minutes** (i.e., **180 seconds**).【1】【2】
  - judge: Provides the default delay (3 minutes) but omits the reference fact that it can be overridden with countdown.
  - retrieved: tasks.rst, configuration.rst, application.rst, configuration.rst, tasks.rst
- **ex11** (exact_match, incorrect, hit=True, coverage=1.0): What Dockerfile RUN line caches pip downloads between builds?
  - expected: RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
  - got: The Dockerfile line that caches the pip downloads between builds is:

```
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt
```

This `RUN` instruction installs the dependencies from `requirements.txt` and, because the `requirements.txt` file is copied first and rarely changes, Docker re‑uses the cached layer for this step in subsequent builds, thereby re‑using the previously down
  - judge: The answer gives a different RUN line without the required --mount=type=cache option, contradicting the reference.
  - retrieved: best-practices.md, optimize.md, invalidation.md, docker.md, docker.md
- **mu02** (multi_hop, incorrect, hit=False, coverage=0.0): A pytest suite in GitHub Actions runs for 7 hours on a GitHub-hosted runner. What happens, and what is the default job timeout?
  - expected: GitHub-hosted jobs are limited to 6 hours of execution time, so the job is terminated and fails; the default timeout-minutes is 360.
  - got: The retrieved documents do not contain the answer.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: github-hosted-runners.md, github-hosted-runners.md, actions-runner-controller.md, secure-use.md, understand-github-actions.md
- **mu03** (multi_hop, incorrect, hit=True, coverage=1.0): In a CI lint step, `ruff check` fails the job. What exit code caused it, and how do you make that step non-blocking in GitHub Actions without changing Ruff's exit code?
  - expected: ruff check exits 1 when violations are found; set continue-on-error: true on the step.
  - got: The retrieved documents do not contain the answer. The draft answer could not be verified: does not answer the question: The answer does not explain how to make the step non‑blocking in GitHub Actions.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: linter.md, integrations.md, linter.md, integrations.md, linter.md, migrate-from-travis-ci.md, workflow-syntax.md, workflow-syntax.md
- **mu05** (multi_hop, incorrect, hit=True, coverage=0.5): To speed up a Python CI pipeline, how do you cache pip in the Dockerfile build, and after how long does GitHub evict unused Actions cache entries?
  - expected: Use RUN --mount=type=cache,target=/root/.cache/pip in the Dockerfile; GitHub removes cache entries not accessed in over 7 days.
  - got: The retrieved documents do not contain the answer.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: migrate-from-gitlab-cicd.md, cache.md, invalidation.md, gha.md, optimize.md
