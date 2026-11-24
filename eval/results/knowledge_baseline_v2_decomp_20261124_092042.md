# Knowledge eval: baseline+v2_decomp

| baseline+v2_decomp | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 3 | 100% | 100% | 100% | 0% | 5.9 | 3115 |
| exact_match | 1 | 100% | 100% | 100% | 0% | 84.7 | 4115 |
| multi_hop | 5 | 80% | 40% | 60% | 20% | 24.0 | 2742 |
| all | 9 | 89% | 67% | 78% | 11% | 24.7 | 3019 |

Decomposer: split 5/5 multi_hop questions, 4/4 others (should be 0)

Wrong answers delivered (incorrect and not a refusal): 0/9

## Failures

- **mu02** (multi_hop, incorrect, hit=False, coverage=0.0): A pytest suite in GitHub Actions runs for 7 hours on a GitHub-hosted runner. What happens, and what is the default job timeout?
  - expected: GitHub-hosted jobs are limited to 6 hours of execution time, so the job is terminated and fails; the default timeout-minutes is 360.
  - got: The retrieved documents do not contain the answer.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: github-hosted-runners.md, github-hosted-runners.md, actions-runner-controller.md, secure-use.md, understand-github-actions.md, usage.rst, migrate-from-travis-ci.md, output.rst
- **mu05** (multi_hop, partial, hit=True, coverage=1.0): To speed up a Python CI pipeline, how do you cache pip in the Dockerfile build, and after how long does GitHub evict unused Actions cache entries?
  - expected: Use RUN --mount=type=cache,target=/root/.cache/pip in the Dockerfile; GitHub removes cache entries not accessed in over 7 days.
  - got: To cache **pip dependencies in the Dockerfile build**, use a **cache mount** with the target path for pip’s cache (e.g., `/root/.cache/pip`):

```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
```

---
**Missing:** The context does **not** specify the exact GitHub Actions cache eviction timeframe for unused entries. [7] only mentions usage limits an
  - judge: Provides the correct cache mount command but omits and contradicts the reference fact that GitHub evicts unused cache entries after 7 days.
  - retrieved: migrate-from-gitlab-cicd.md, cache.md, invalidation.md, gha.md, optimize.md, python.md, dependency-caching.md, optimize.md
