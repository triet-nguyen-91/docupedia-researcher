"""
embedder/embedding_model.py

Loads the multilingual-e5-base embedding model via sentence-transformers and
exposes two helpers:
  - embed_documents(texts)  → list[list[float]]   (for indexing, prefix "passage: ")
  - embed_query(query)      → list[float]          (for querying, prefix "query: ")

The model is lazy-loaded on first use and kept in memory as a module-level
singleton for the lifetime of the process.
"""

import logging
import sys
from pathlib import Path

from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """Return (and lazily load) the embedding model singleton."""
    global _model
    if _model is None:
        logger.info(f"Loading embedding model: {config.EMBEDDING_MODEL}")
        _model = SentenceTransformer(config.EMBEDDING_MODEL)
        logger.info("Embedding model ready.")
    return _model


def embed_documents(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of document strings for storage.

    Applies the ``passage:`` prefix required by intfloat/multilingual-e5-*
    models, and returns unit-normalised vectors.
    """
    model = get_model()
    prefixed = [f"passage: {t}" for t in texts]
    return model.encode(prefixed, normalize_embeddings=True).tolist()


def embed_query(query: str) -> list[float]:
    """
    Embed a single query string for retrieval.

    Applies the ``query:`` prefix required by intfloat/multilingual-e5-*
    models, and returns a unit-normalised vector.
    """
    model = get_model()
    return model.encode(f"query: {query}", normalize_embeddings=True).tolist()
