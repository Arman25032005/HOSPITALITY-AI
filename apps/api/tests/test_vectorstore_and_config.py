import uuid
from datetime import date

import pytest

from app.config import Settings
from app.rag.embeddings import ConfigError, HashingEmbedder
from app.rag.vectorstore import (
    IndexedChunk, PineconeVectorStore, SearchFilters, get_vector_store,
)

ORG, HOTEL, DOC = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class FakeIndex:
    def __init__(self, as_objects=False):
        self.upserts, self.deletes, self.queries = [], [], []
        self.as_objects = as_objects

    def upsert(self, vectors, namespace):
        self.upserts.append((namespace, vectors))

    def delete(self, filter, namespace):
        self.deletes.append((namespace, filter))

    def query(self, vector, top_k, filter, include_metadata, namespace):
        self.queries.append((namespace, filter, top_k))
        ids = [str(uuid.uuid4()), "not-finite"]
        if self.as_objects:
            class M:
                def __init__(self, i, s):
                    self.id, self.score = i, s

            class R:
                matches = [M(ids[0], 0.9)]

            return R()
        return {"matches": [{"id": ids[0], "score": 0.9}]}


def chunk(conf=False):
    return IndexedChunk(uuid.uuid4(), ORG, HOTEL, DOC, [0.1] * 4, "m1", "policy", "en", conf, 20260101)


def filters(conf):
    return SearchFilters(ORG, HOTEL, conf, date(2026, 3, 1), "m1", language="en",
                         doc_types=("policy",))


def test_pinecone_upsert_uses_tenant_namespace_and_batches():
    idx = FakeIndex()
    PineconeVectorStore(idx).upsert([chunk() for _ in range(150)])
    assert {ns for ns, _ in idx.upserts} == {f"{ORG}:{HOTEL}"}
    assert [len(v) for _, v in idx.upserts] == [100, 50]
    assert idx.upserts[0][1][0]["metadata"]["document_id"] == str(DOC)


@pytest.mark.parametrize("objects", [False, True])
def test_pinecone_query_filters_by_audience(objects):
    idx = FakeIndex(as_objects=objects)
    store = PineconeVectorStore(idx)
    assert len(store.query([0.1] * 4, filters(False), 5)) == 1
    ns, flt, _ = idx.queries[-1]
    assert ns == f"{ORG}:{HOTEL}"
    assert flt["is_confidential"] == {"$eq": False}
    assert flt["effective"] == {"$lte": 20260301}
    assert flt["embedding_model"] == {"$eq": "m1"} and flt["language"] == {"$eq": "en"}
    assert flt["doc_type"] == {"$in": ["policy"]}
    store.query([0.1] * 4, filters(True), 5)
    assert "is_confidential" not in idx.queries[-1][1]


def test_pinecone_delete_scopes_to_document_and_namespace():
    idx = FakeIndex()
    PineconeVectorStore(idx).delete_document(ORG, HOTEL, DOC)
    assert idx.deletes == [(f"{ORG}:{HOTEL}", {"document_id": {"$eq": str(DOC)}})]


def test_pinecone_requires_api_key():
    with pytest.raises(ConfigError, match="PINECONE_API_KEY"):
        get_vector_store(Settings(vector_store_provider="pinecone", pinecone_api_key=""), None)


def test_local_store_refused_in_production():
    with pytest.raises(ConfigError, match="production"):
        get_vector_store(Settings(environment="production", vector_store_provider="local"), None)


def test_unknown_providers_fail_loudly():
    with pytest.raises(ConfigError):
        get_vector_store(Settings(vector_store_provider="faiss"), None)
    from app.rag.embeddings import get_embedder

    with pytest.raises(ConfigError):
        get_embedder(Settings(embedding_provider="openai-ish"))
    assert isinstance(get_embedder(Settings(embedding_provider="hashing")), HashingEmbedder)


def test_production_startup_guards():
    good = dict(environment="production", app_secret_key="x" * 48,
                vector_store_provider="pinecone", pinecone_api_key="k",
                embedding_provider="sentence-transformers", metrics_token="m" * 24)
    Settings(**good).validate_for_runtime()
    for bad in [{"app_secret_key": "dev-only-insecure-secret-change-me"},
                {"vector_store_provider": "local"}, {"pinecone_api_key": ""},
                {"embedding_provider": "hashing"}, {"metrics_token": ""}]:
        with pytest.raises(RuntimeError):
            Settings(**{**good, **bad}).validate_for_runtime()
