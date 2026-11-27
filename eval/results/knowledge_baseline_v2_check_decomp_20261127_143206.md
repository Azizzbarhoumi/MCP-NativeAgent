# Knowledge eval: baseline+v2_check_decomp

Answer model: openrouter/openai/gpt-oss-20b, judge: groq

| baseline+v2_check_decomp | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 92% | 8% | 10.0 | 4324 |
| exact_match | 11 | 100% | 100% | 82% | 9% | 8.5 | 5227 |
| multi_hop | 8 | 88% | 62% | 75% | 12% | 17.3 | 8514 |
| all | 31 | 97% | 90% | 84% | 10% | 11.3 | 5726 |

Checker: retried 4/31, refused 0, correct after retry 2

Decomposer: split 5/8 multi_hop questions, 3/23 others (should be 0)

Wrong answers delivered (incorrect and not a refusal): 1/31

## Failures

- **lo07** (lookup, partial, hit=True, coverage=1.0): What is the default delay before a Celery task is retried?
  - expected: 3 minutes (Task.default_retry_delay); it can be overridden with countdown.
  - got: The default retry delay is **3 minutes** (180 seconds) before a Celery task is retried.  
[1][2]
  - judge: Provides the default delay of 3 minutes but omits the reference's note that it can be overridden with countdown.
  - retrieved: tasks.rst, configuration.rst, application.rst, configuration.rst, tasks.rst
- **ex06** (exact_match, partial, hit=True, coverage=1.0): Which flag makes Ruff apply unsafe fixes too?
  - expected: --unsafe-fixes together with --fix (or the unsafe-fixes setting).
  - got: The flag that enables Ruff to apply unsafe fixes is `--unsafe-fixes`【1】【2】.
  - judge: Mentions --unsafe-fixes but omits that it must be used with --fix or the setting.
  - retrieved: linter.md, faq.md, features.md, linter.md, linter.md
- **ex11** (exact_match, incorrect, hit=True, coverage=1.0): What Dockerfile RUN line caches pip downloads between builds?
  - expected: RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
  - got: The Dockerfile line that is used to install the Python dependencies (and is the one that Docker can cache between builds) is:

```
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt
```

[4] shows this exact `RUN` instruction in the FastAPI example Dockerfile.  
The passage explains that copying the `requirements.txt` file first allows Docker to reuse the layer that runs this `RUN`
  - judge: The answer gives a different RUN command without the cache mount, contradicting the reference.
  - retrieved: best-practices.md, optimize.md, invalidation.md, docker.md, docker.md, python.md, migrate-from-circleci.md, powershell.md, optimize.md
- **mu02** (multi_hop, incorrect, hit=False, coverage=0.0): A pytest suite in GitHub Actions runs for 7 hours on a GitHub-hosted runner. What happens, and what is the default job timeout?
  - expected: GitHub-hosted jobs are limited to 6 hours of execution time, so the job is terminated and fails; the default timeout-minutes is 360.
  - got: The retrieved documents do not contain the answer.
  - judge: declined: answer starts with the 'not found' sentence
  - retrieved: github-hosted-runners.md, github-hosted-runners.md, actions-runner-controller.md, secure-use.md, understand-github-actions.md
- **mu06** (multi_hop, partial, hit=True, coverage=1.0): A CI run fails because conftest.py cannot be imported. What pytest exit code does that produce, and which command-line flag controls how pytest imports test modules and conftest files?
  - expected: Exit code 4; the --import-mode flag (default prepend).
  - got: The failure to import a `conftest.py` file is reported by pytest with exit code **4** – “pytest command line usage error, including a plugin that cannot be found or a `conftest.py` that fails to import”【2】.  

The command‑line flag that controls how pytest imports test modules and `conftest.py` files is **`--import-mode`** (e.g., `--import-mode=prepend`, `--import-mode=append`, or `--import-mode=i
  - judge: Provides correct exit code and flag but omits the default value (prepend) mentioned in the reference.
  - retrieved: writing_plugins.rst, exit-codes.rst, pythonpath.rst, pythonpath.rst, reference.rst, pythonpath.rst, goodpractices.rst, pythonpath.rst, pythonpath.rst, goodpractices.rst
