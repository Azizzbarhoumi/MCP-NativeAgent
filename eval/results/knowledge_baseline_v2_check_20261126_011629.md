# Knowledge eval: baseline+v2_check

Answer model: groq/openai/gpt-oss-20b, judge: groq

| baseline+v2_check | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 100% | 0% | 20.3 | 3607 |
| exact_match | 11 | 100% | 100% | 91% | 0% | 25.1 | 3698 |
| multi_hop | 8 | 88% | 75% | 62% | 0% | 54.1 | 7408 |
| all | 31 | 97% | 94% | 87% | 0% | 30.7 | 4620 |

Checker: retried 4/31, refused 1, correct after retry 3

Wrong answers delivered (incorrect and not a refusal): 2/31

## Failures

- **ex11** (exact_match, incorrect, hit=True, coverage=1.0): What Dockerfile RUN line caches pip downloads between builds?
  - expected: RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
  - got: The Dockerfile line that caches pip downloads between builds is:

```
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt
```

[4][5]
  - judge: The answer does not include the required --mount=type=cache option and instead uses --no-cache-dir, contradicting the reference.
  - retrieved: best-practices.md, optimize.md, invalidation.md, docker.md, docker.md
- **mu02** (multi_hop, incorrect, hit=False, coverage=0.0): A pytest suite in GitHub Actions runs for 7 hours on a GitHub-hosted runner. What happens, and what is the default job timeout?
  - expected: GitHub-hosted jobs are limited to 6 hours of execution time, so the job is terminated and fails; the default timeout-minutes is 360.
  - got: The retrieved documents do not contain the answer.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: github-hosted-runners.md, github-hosted-runners.md, actions-runner-controller.md, secure-use.md, understand-github-actions.md
- **mu03** (multi_hop, incorrect, hit=True, coverage=1.0): In a CI lint step, `ruff check` fails the job. What exit code caused it, and how do you make that step non-blocking in GitHub Actions without changing Ruff's exit code?
  - expected: ruff check exits 1 when violations are found; set continue-on-error: true on the step.
  - got: The retrieved documents do not contain the answer. The draft answer could not be verified: What you need to do to make that step non‑blocking is not covered in the provided passages.; does not answer the question: The answer does not provide how to make the step non‑blocking.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: linter.md, integrations.md, linter.md, integrations.md, linter.md, migrate-from-travis-ci.md, workflow-syntax.md, workflow-syntax.md
- **mu05** (multi_hop, incorrect, hit=True, coverage=0.5): To speed up a Python CI pipeline, how do you cache pip in the Dockerfile build, and after how long does GitHub evict unused Actions cache entries?
  - expected: Use RUN --mount=type=cache,target=/root/.cache/pip in the Dockerfile; GitHub removes cache entries not accessed in over 7 days.
  - got: Use cache mounts in your Dockerfile to persist the pip cache so that subsequent builds can reuse the downloaded packages. This is described in the “Use cache mounts” section, which states that cache mounts let you specify a persistent package cache to be used during builds, especially for package‑manager installs [5].

GitHub evicts unused Actions cache entries after a certain period of time, but 
  - judge: The answer omits the specific RUN --mount=type=cache,target=/root/.cache/pip command and incorrectly states the cache eviction period is unspecified, contradicting the reference's 7‑day figure.
  - retrieved: migrate-from-gitlab-cicd.md, cache.md, invalidation.md, gha.md, optimize.md
