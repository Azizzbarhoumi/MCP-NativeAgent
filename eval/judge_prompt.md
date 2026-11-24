You are grading an answer from a documentation assistant against a reference answer.

Question:
{question}

Reference answer (verified by a human against the documentation):
{expected}

Assistant answer:
{answer}

Grade ONLY factual agreement with the reference:
- "correct": contains every fact in the reference (wording may differ) and nothing that contradicts it.
- "partial": contains some but not all reference facts, or adds a claim that contradicts the reference.
- "incorrect": misses the key fact, contradicts it, or says the documents do not contain the answer.
Extra correct detail is fine. Ignore citation markers like [1].

Reply with ONLY this JSON, no other text:
{{"verdict": "correct" | "partial" | "incorrect", "reason": "<one short sentence>"}}
