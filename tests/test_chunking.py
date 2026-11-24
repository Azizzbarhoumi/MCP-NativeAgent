"""Chunking tests: heading split, overlap, stable ids, code handling, benchmark isolation."""

from pathlib import Path

import pytest

from knowledge.ingest.chunk import chunk_document, split_body, split_sections
from knowledge.ingest.parse import shorten_long_code
from knowledge.sources.base import PROJECT_ROOT, Document, assert_allowed

DOC = """# Guide
Intro text that is long enough to be kept as a chunk on its own, really.

## Install
Run pip install and then check the version output carefully please.

```python
# this is a comment, not a heading
x = 1
```

## Usage
Use it like this and read the error message when it fails loudly.
"""


def doc(text=DOC):
    return Document(id="src:guide.md", text=text, source="src", path="guide.md", url=None, collection="c")


def test_split_by_headings_with_path():
    titles = [t for t, _ in split_sections(DOC)]
    assert titles == ["Guide", "Guide > Install", "Guide > Usage"]


def test_hash_inside_code_is_not_a_heading():
    body = dict(split_sections(DOC))["Guide > Install"]
    assert "# this is a comment" in body


def test_chunk_text_starts_with_section_title():
    chunks = chunk_document(doc(), max_chars=1600, overlap=200, min_chars=10)
    assert chunks[1].text.startswith("Guide > Install\n\n")


def test_long_section_split_with_overlap():
    paras = [f"Sentence number {i} is here. It ends with a period." for i in range(30)]
    pieces = split_body("\n\n".join(paras), max_chars=300, overlap=120)
    assert len(pieces) > 1
    assert all(len(p) <= 300 + 120 for p in pieces)
    # the start of each piece repeats text from the end of the previous one
    for prev, nxt in zip(pieces, pieces[1:]):
        assert nxt.split("\n\n")[0] in prev


def test_code_block_is_never_cut_by_overlap():
    code = "```yaml\n" + "\n".join(f"key{i}: value" for i in range(10)) + "\n```"
    body = "\n\n".join(["Some prose before. " * 10, code, "Prose after. " * 30])
    for piece in split_body(body, max_chars=300, overlap=120):
        assert piece.count("```") % 2 == 0  # fences always balanced


def test_ids_are_stable_and_unique():
    a = chunk_document(doc(), 1600, 200, 10)
    b = chunk_document(doc(), 1600, 200, 10)
    assert [c.id for c in a] == [c.id for c in b]
    assert len({c.id for c in a}) == len(a)


def test_long_code_block_is_replaced_short_one_kept():
    long_block = "```\n" + "\n".join(["line"] * 40) + "\n```"
    short_block = "```\nERROR: file not found\n```"
    out = shorten_long_code(f"{long_block}\n\n{short_block}", max_lines=25)
    assert "[40-line code block omitted]" in out
    assert "ERROR: file not found" in out


def test_benchmark_folder_is_refused():
    with pytest.raises(PermissionError):
        assert_allowed(PROJECT_ROOT / "eval" / "ci_runs" / "run_1.log")
    assert_allowed(PROJECT_ROOT / "knowledge" / "corpus" / "x.md")  # allowed
