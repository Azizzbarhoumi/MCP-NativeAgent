"""Loads knowledge/config.yaml into typed settings (one source of truth for the pipeline)."""

from pathlib import Path

import yaml
from pydantic import BaseModel

CONFIG_FILE = Path(__file__).resolve().parent / "config.yaml"


class Embedding(BaseModel):
    dense_model: str
    sparse_model: str
    batch_size: int = 32


class Chunking(BaseModel):
    max_chars: int
    overlap_chars: int
    min_chars: int
    max_code_lines: int


class Retrieval(BaseModel):
    k: int = 5


class KnowledgeConfig(BaseModel):
    collection: str
    embedding: Embedding
    chunking: Chunking
    retrieval: Retrieval


def load_config(path: Path = CONFIG_FILE) -> KnowledgeConfig:
    return KnowledgeConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
