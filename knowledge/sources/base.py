"""Source adapter interface. The ONLY place that knows where documents come from.

Ingestion core (chunk -> embed -> store) sees nothing but `Document`s, so it has no idea
whether a doc came from GitHub docs, Docker docs, a wiki or a PDF folder. Adding a new
domain = writing one adapter, no change to the core.
"""

from pathlib import Path
from typing import Iterator, Protocol

from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = PROJECT_ROOT / "eval"  # benchmark data lives here; never indexed (ADR 0001)


class Document(BaseModel):
    """One source document, already cleaned to plain Markdown-like text."""

    id: str            # stable: "<source>:<path>", so re-ingestion overwrites, never duplicates
    text: str
    source: str        # adapter name, e.g. "pytest_docs"
    path: str          # path inside the source, e.g. "doc/en/how-to/fixtures.rst"
    url: str | None    # public URL of the page, if derivable
    collection: str    # target Qdrant collection, e.g. "dev_docs"


class SourceAdapter(Protocol):
    name: str

    def documents(self, collection: str) -> Iterator[Document]:
        """Yield cleaned Documents. Must never read anything under eval/."""
        ...


def assert_allowed(path: Path) -> None:
    """Hard guard for ADR 0001: raise if a path is inside eval/ (checked by every adapter)."""
    if path.resolve().is_relative_to(FORBIDDEN.resolve()):
        raise PermissionError(f"Refusing to ingest benchmark data: {path}")
