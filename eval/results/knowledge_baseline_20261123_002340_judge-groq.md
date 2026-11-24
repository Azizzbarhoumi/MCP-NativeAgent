# Re-judged: knowledge_baseline_20261123_002340.json

| baseline+v1 (judge groq) | n | retrieval hit | full coverage | correct | partial | latency s | tokens |
|---|---|---|---|---|---|---|---|
| lookup | 12 | 100% | 100% | 58% | 8% | 28.1 | 1448 |
| exact_match | 11 | 100% | 100% | 18% | 9% | 29.9 | 1478 |
| multi_hop | 8 | 88% | 38% | 0% | 0% | 30.2 | 1520 |
| all | 31 | 97% | 84% | 29% | 6% | 29.3 | 1477 |

**Judge agreement mistral vs groq: 94%** (2 of 31 verdicts changed)

- **lo07**: mistral said correct, groq says partial: Provides the default delay (3 minutes) but omits the reference fact that it can be overridden with countdown.
- **mu06**: mistral said correct, groq says incorrect: Assistant correctly states exit code 4 but incorrectly claims there is no pytest flag, contradicting the reference that specifies the --import-mode flag.
