from typing import List

from qdrant_client import QdrantClient
from qdrant_client.http import models as rest


def ensure_collection(client: QdrantClient, collection: str, vector_size: int) -> None:
    collections = client.get_collections().collections
    if any(col.name == collection for col in collections):
        info = client.get_collection(collection)
        existing_size = info.config.params.vectors.size
        if existing_size != vector_size:
            raise ValueError(
                f"Qdrant collection '{collection}' has vector size {existing_size}, "
                f"expected {vector_size}. Clear vectors or use a matching embedding model."
            )
        return
    client.create_collection(
        collection_name=collection,
        vectors_config=rest.VectorParams(size=vector_size, distance=rest.Distance.COSINE),
    )
    client.create_payload_index(collection_name=collection, field_name="doc_id", field_schema="keyword")


def delete_by_doc_id(client: QdrantClient, collection: str, doc_id: str) -> None:
    client.delete(
        collection_name=collection,
        points_selector=rest.Filter(
            must=[rest.FieldCondition(key="doc_id", match=rest.MatchValue(value=doc_id))]
        ),
    )


def upsert_vectors(
    client: QdrantClient,
    collection: str,
    ids: List[str],
    vectors: List[List[float]],
    payloads: List[dict],
    batch_size: int = 100,
) -> None:
    """
    Upsert points into Qdrant in batches to avoid huge JSON payloads.
    batch_size can be tuned (100 is a safe default).
    """
    if not (len(ids) == len(vectors) == len(payloads)):
        raise ValueError("ids, vectors and payloads must have equal length")

    # Iterate in batches
    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i : i + batch_size]
        batch_vectors = vectors[i : i + batch_size]
        batch_payloads = payloads[i : i + batch_size]
        client.upsert(
            collection_name=collection,
            points=rest.Batch(ids=batch_ids, vectors=batch_vectors, payloads=batch_payloads),
        )
