"""Local embeddings with fastembed (ONNX, no PyTorch). Model names come from config.yaml."""

from functools import cached_property

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import models


class DenseEmbedder:
    def __init__(self, model: str, batch_size: int = 32):
        self.model_name, self.batch_size = model, batch_size

    @cached_property
    def model(self) -> TextEmbedding:
        return TextEmbedding(self.model_name)  # downloads once, then cached locally

    @cached_property
    def dim(self) -> int:
        return len(self.embed_query("dimension probe"))

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self.model.passage_embed(texts, batch_size=self.batch_size)]

    def embed_query(self, text: str) -> list[float]:
        # query_embed applies the model's query instruction where it has one (bge does).
        return next(iter(self.model.query_embed(text))).tolist()


class SparseEmbedder:
    """BM25 keyword vectors: one entry per word (token id -> term weight). Same fastembed
    package, tiny download (stopword lists), no neural network."""

    def __init__(self, model: str, batch_size: int = 32):
        self.model_name, self.batch_size = model, batch_size

    @cached_property
    def model(self) -> SparseTextEmbedding:
        return SparseTextEmbedding(self.model_name)

    @staticmethod
    def _to_qdrant(e) -> models.SparseVector:
        return models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())

    def embed_passages(self, texts: list[str]) -> list[models.SparseVector]:
        return [self._to_qdrant(e) for e in self.model.passage_embed(texts, batch_size=self.batch_size)]

    def embed_query(self, text: str) -> models.SparseVector:
        return self._to_qdrant(next(iter(self.model.query_embed(text))))
