# Knowledge eval: baseline+v2_check_decomp

Answer model: openrouter/openai/gpt-oss-20b, judge: groq

| baseline+v2_check_decomp | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 92% | 8% | 8.1 | 4136 |
| exact_match | 11 | 100% | 100% | 91% | 0% | 7.3 | 4193 |
| multi_hop | 8 | 100% | 62% | 88% | 0% | 13.9 | 7342 |
| all | 31 | 100% | 90% | 90% | 3% | 9.3 | 4984 |

Checker: retried 2/31, refused 1, correct after retry 1

Decomposer: split 5/8 multi_hop questions, 2/23 others (should be 0)

Wrong answers delivered (incorrect and not a refusal): 1/31

## Failures

- **lo07** (lookup, partial, hit=True, coverage=1.0): What is the default delay before a Celery task is retried?
  - expected: 3 minutes (Task.default_retry_delay); it can be overridden with countdown.
  - got: The default retry delay for a Celery task is **3 minutes** (180 seconds)【1】【2】.
  - judge: Provides the default delay of 3 minutes but omits the reference fact that it can be overridden with countdown.
  - retrieved: tasks.rst, configuration.rst, application.rst, configuration.rst, tasks.rst
- **ex11** (exact_match, incorrect, hit=True, coverage=1.0): What Dockerfile RUN line caches pip downloads between builds?
  - expected: RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
  - got: The Dockerfile line that caches the pip downloads between builds is:

```
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt
```

This instruction installs the dependencies listed in `requirements.txt` and, because it is a separate layer, Docker will reuse the layer (and thus the downloaded packages) on subsequent builds unless the `requirements.txt` file changes. [4]
  - judge: The answer gives a different RUN line without the required --mount=type=cache and even disables caching, contradicting the reference.
  - retrieved: best-practices.md, optimize.md, invalidation.md, docker.md, docker.md, python.md, best-practices.md, powershell.md
- **mu03** (multi_hop, incorrect, hit=True, coverage=0.5): In a CI lint step, `ruff check` fails the job. What exit code caused it, and how do you make that step non-blocking in GitHub Actions without changing Ruff's exit code?
  - expected: ruff check exits 1 when violations are found; set continue-on-error: true on the step.
  - got: The retrieved documents do not contain the answer. The draft answer could not be verified: does not answer the question: The answer does not provide a method to make the step non-blocking.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: linter.md, integrations.md, linter.md, integrations.md, linter.md, migration.md, migrate-from-travis-ci.md, linter.md, integrations.md, formatter.md
