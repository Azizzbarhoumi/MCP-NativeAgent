# Knowledge eval: baseline+v2_check

Answer model: openrouter/openai/gpt-oss-20b, judge: groq

| baseline+v2_check | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 100% | 0% | 8.9 | 3592 |
| exact_match | 11 | 100% | 100% | 82% | 9% | 6.1 | 3665 |
| multi_hop | 8 | 88% | 75% | 62% | 0% | 19.3 | 8590 |
| all | 31 | 97% | 94% | 84% | 3% | 10.6 | 4908 |

Checker: retried 5/31, refused 2, correct after retry 3

Wrong answers delivered (incorrect and not a refusal): 1/31

## Failures

- **ex06** (exact_match, partial, hit=True, coverage=1.0): Which flag makes Ruff apply unsafe fixes too?
  - expected: --unsafe-fixes together with --fix (or the unsafe-fixes setting).
  - got: The flag that tells Ruff to apply unsafe fixes is `--unsafe-fixes`【1】【2】.
  - judge: Mentions the --unsafe-fixes flag but omits that it must be used with --fix or the unsafe-fixes setting.
  - retrieved: linter.md, faq.md, features.md, linter.md, linter.md
- **ex11** (exact_match, incorrect, hit=True, coverage=1.0): What Dockerfile RUN line caches pip downloads between builds?
  - expected: RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
  - got: The Dockerfile line that caches pip downloads between builds is:

```
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt
```

This line is placed after copying only the `requirements.txt` file, so Docker can reuse the cached layer for the pip install step when the requirements file hasn’t changed, thereby re‑using the previously downloaded packages between builds. [4][5]
  - judge: The answer gives a different RUN line without the required --mount cache and uses --no-cache-dir, contradicting the reference.
  - retrieved: best-practices.md, optimize.md, invalidation.md, docker.md, docker.md
- **mu02** (multi_hop, incorrect, hit=False, coverage=0.0): A pytest suite in GitHub Actions runs for 7 hours on a GitHub-hosted runner. What happens, and what is the default job timeout?
  - expected: GitHub-hosted jobs are limited to 6 hours of execution time, so the job is terminated and fails; the default timeout-minutes is 360.
  - got: The retrieved documents do not contain the answer.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: github-hosted-runners.md, github-hosted-runners.md, actions-runner-controller.md, secure-use.md, understand-github-actions.md
- **mu05** (multi_hop, incorrect, hit=True, coverage=1.0): To speed up a Python CI pipeline, how do you cache pip in the Dockerfile build, and after how long does GitHub evict unused Actions cache entries?
  - expected: Use RUN --mount=type=cache,target=/root/.cache/pip in the Dockerfile; GitHub removes cache entries not accessed in over 7 days.
  - got: The retrieved documents do not contain the answer. The draft answer could not be verified: you can add a cache mount for the pip cache directory in the Dockerfile
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: migrate-from-gitlab-cicd.md, cache.md, invalidation.md, gha.md, optimize.md, dependency-caching.md, dependency-caching.md, gha.md, limits.md
- **mu08** (multi_hop, incorrect, hit=True, coverage=1.0): Your FastAPI tests fail with an import error for httpx in CI. What must be installed, and which GitHub Actions debug variable gives more detailed step logs to confirm it?
  - expected: Install httpx (needed by TestClient); set ACTIONS_STEP_DEBUG to true.
  - got: The retrieved documents do not contain the answer. The draft answer could not be verified: pip install httpx
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: testing.md, first-steps.md, index.md, first-steps.md, index.md, enable-debug-logging.md, troubleshoot-workflows.md, use-workflow-run-logs.md, workflow-commands.md, variables.md
