"""
embedder/chroma_store.py

ChromaDB storage and retrieval for document chunks.

Exposes:
  - upsert_chunks(chunks)      → store (or update) embedded chunks
  - query(query_text, n)       → semantic search, returns list of result dicts
  - get_collection_stats()     → {"count": int}
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import chromadb

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from embedder.embedding_model import embed_documents, embed_query as _embed_query

logger = logging.getLogger(__name__)

# Chunks are sent to ChromaDB in batches to avoid OOM on large corpora.
_BATCH_SIZE = 64

_client: chromadb.PersistentClient | None = None
_collection = None


def get_collection():
    """Return (and lazily initialise) the ChromaDB collection singleton."""
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        _collection = _client.get_or_create_collection(
            name=config.CHROMA_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            f"ChromaDB collection '{config.CHROMA_COLLECTION_NAME}' ready "
            f"({_collection.count()} existing chunks)."
        )
    return _collection


def upsert_chunks(chunks: list[dict]) -> None:
    """
    Embed and upsert a list of chunk dicts into ChromaDB.

    Chunk dict format (from ``processor.chunker.chunk_page``):
        {"id": str, "text": str, "metadata": dict}

    Uses ``upsert`` so re-running the pipeline is idempotent.
    """
    if not chunks:
        return

    collection = get_collection()

    for batch_start in range(0, len(chunks), _BATCH_SIZE):
        batch = chunks[batch_start : batch_start + _BATCH_SIZE]
        ids = [c["id"] for c in batch]
        texts = [c["text"] for c in batch]
        metadatas = [c["metadata"] for c in batch]
        embeddings = embed_documents(texts)

        collection.upsert(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        logger.debug(
            f"Upserted batch {batch_start // _BATCH_SIZE + 1}: {len(batch)} chunks"
        )


def query(query_text: str, n_results: int = 5) -> list[dict]:
    """
    Perform a semantic search against the ChromaDB collection.

    Args:
        query_text: Natural-language query (Vietnamese or English).
        n_results:  Number of top results to return.

    Returns:
        List of dicts: [{"text": str, "metadata": dict, "distance": float}, ...]
        Sorted by ascending cosine distance (lower = more similar).
    """
    collection = get_collection()
    q_emb = _embed_query(query_text)

    results = collection.query(
        query_embeddings=[q_emb],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    output = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        output.append({"text": doc, "metadata": meta, "distance": dist})
    return output


def get_collection_stats() -> dict:
    """Return basic statistics about the collection."""
    return {"count": get_collection().count()}
