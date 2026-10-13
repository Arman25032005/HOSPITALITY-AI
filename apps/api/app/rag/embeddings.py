import hashlib
import math
from typing import Protocol

from app.config import Settings
from app.rag.text import tokenize

DIM = 384


class ConfigError(RuntimeError):
    """Required provider configuration is missing; fail loudly rather than degrade."""


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic signed feature-hashing of unigrams+bigrams (real lexical vectors).

    It captures word overlap, NOT semantics. Intended for local development and automated
    tests; production configuration rejects it (see Settings.validate_for_runtime).
    """

    name = "hashing-v2"
    dim = DIM

    def _vec(self, text: str) -> list[float]:
        toks = tokenize(text)
        feats: dict[str, float] = {}
        for t in toks:
            feats[t] = feats.get(t, 0.0) + 1.0
        for a, b in zip(toks, toks[1:], strict=False):
            feats[f"{a}_{b}"] = feats.get(f"{a}_{b}", 0.0) + 0.5
        v = [0.0] * self.dim
        for f, c in feats.items():
            h = hashlib.blake2b(f.encode(), digest_size=8).digest()
            n = int.from_bytes(h, "big")
            v[n % self.dim] += (1.0 if (n >> 63) & 1 else -1.0) * (1.0 + math.log(c))
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ConfigError(
                "EMBEDDING_PROVIDER=sentence-transformers requires `pip install "
                "sentence-transformers`") from exc
        self._model = SentenceTransformer(model_name)
        self.name = f"st:{model_name}"
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([text], normalize_embeddings=True)[0].tolist()


def get_embedder(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "hashing":
        return HashingEmbedder()
    if settings.embedding_provider == "sentence-transformers":
        return SentenceTransformerEmbedder(settings.embedding_model)
    raise ConfigError(f"Unknown EMBEDDING_PROVIDER {settings.embedding_provider!r}")
