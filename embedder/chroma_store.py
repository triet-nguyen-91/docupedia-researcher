"""
embedder/chroma_store.py

ChromaDB storage and retrieval for document chunks.

Exposes:
  - upsert_chunks(chunks)      → store (or update) embedded chunks
    - update_chunk_metadatas()   → update metadata for existing chunk IDs only
    - query(query_text, n)       → semantic search, returns list of result dicts
    - has_indexed_space(key)     → whether the collection contains space_key metadata
  - get_collection_stats()     → {"count": int}
"""

from __future__ import annotations

from collections.abc import Sequence
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


def update_chunk_metadatas(chunks: list[dict]) -> None:
    """Update metadata for existing chunk IDs without recomputing embeddings."""
    if not chunks:
        return

    collection = get_collection()

    for batch_start in range(0, len(chunks), _BATCH_SIZE):
        batch = chunks[batch_start : batch_start + _BATCH_SIZE]
        ids = [c["id"] for c in batch]
        metadatas = [c["metadata"] for c in batch]

        collection.update(ids=ids, metadatas=metadatas)
        logger.debug(
            f"Updated metadata batch {batch_start // _BATCH_SIZE + 1}: {len(batch)} chunks"
        )


def _normalise_space_keys(space_keys: Sequence[str] | None) -> tuple[str, ...]:
    """Trim and de-duplicate requested search space keys."""
    if not space_keys:
        return ()

    normalised: list[str] = []
    for space_key in space_keys:
        value = str(space_key).strip()
        if value and value not in normalised:
            normalised.append(value)
    return tuple(normalised)


def has_indexed_space(space_key: str) -> bool:
    """Return True when at least one chunk is tagged with the given space key."""
    collection = get_collection()

    try:
        results = collection.get(where={"space_key": space_key}, limit=1)
    except Exception as exc:
        logger.debug(f"Unable to inspect space metadata for '{space_key}': {exc}")
        return False

    return bool(results.get("ids"))


def get_missing_indexed_spaces(space_keys: Sequence[str] | None) -> tuple[str, ...]:
    """Return the requested spaces that do not yet have searchable metadata tags."""
    return tuple(
        space_key
        for space_key in _normalise_space_keys(space_keys)
        if not has_indexed_space(space_key)
    )


def _build_space_filter(space_keys: Sequence[str] | None) -> dict | None:
    """Build a Chroma metadata filter for one or more requested spaces."""
    normalised = _normalise_space_keys(space_keys)
    if not normalised:
        return None
    if len(normalised) == 1:
        return {"space_key": normalised[0]}
    return {"space_key": {"$in": list(normalised)}}


def _normalise_results(results: dict) -> list[dict]:
    """Convert ChromaDB query output into the server's flat result format."""
    documents = results.get("documents") or [[]]
    metadatas = results.get("metadatas") or [[]]
    distances = results.get("distances") or [[]]

    output = []
    for doc, meta, dist in zip(documents[0], metadatas[0], distances[0]):
        output.append({"text": doc, "metadata": meta, "distance": dist})
    return output


def query(
    query_text: str,
    n_results: int = 5,
    space_keys: Sequence[str] | None = None,
) -> list[dict]:
    """
    Perform a semantic search against the ChromaDB collection.

    Args:
        query_text: Natural-language query (Vietnamese or English).
        n_results:  Number of top results to return.
        space_keys: Restrict results to one or more Docupedia spaces.

    Returns:
        List of dicts: [{"text": str, "metadata": dict, "distance": float}, ...]
        Sorted by ascending cosine distance (lower = more similar).
    """
    collection = get_collection()
    q_emb = _embed_query(query_text)

    query_kwargs = {
        "query_embeddings": [q_emb],
        "n_results": n_results,
        "include": ["documents", "metadatas", "distances"],
    }

    space_filter = _build_space_filter(space_keys)
    if space_filter is not None:
        query_kwargs["where"] = space_filter

    results = collection.query(**query_kwargs)
    return _normalise_results(results)


def get_collection_stats() -> dict:
    """Return basic statistics about the collection."""
    return {"count": get_collection().count()}
