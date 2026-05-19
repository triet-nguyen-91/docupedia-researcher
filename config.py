"""
config.py — Central configuration for the Docupedia RAG pipeline.
Loads from .env and provides constants to all other modules.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"

# ---------------------------------------------------------------------------
# Environment — loaded first so SPACE_KEY is available for path construction
# ---------------------------------------------------------------------------
load_dotenv(PROJECT_ROOT / ".env")

DOCUPEDIA_PAT: str = os.environ["DOCUPEDIA_PAT"]
DOCUPEDIA_BASE_URL: str = os.environ["DOCUPEDIA_BASE_URL"].rstrip("/")
SPACE_KEY: str = os.environ["SPACE_KEY"]

# Optional: crawl from a specific root page and all its descendants.
# Set to a Confluence page ID (integer string) to crawl that subtree only.
# Leave empty to crawl the entire space.
_page_id_raw: str = os.getenv("PAGE_ID", "").strip()
PAGE_ID: int | None = int(_page_id_raw) if _page_id_raw else None

# ---------------------------------------------------------------------------
# Paths — all crawl output is scoped under data/<type>/<SPACE_KEY>/
# CHROMA_DIR is shared across all spaces (one searchable vector store).
# ---------------------------------------------------------------------------
RAW_DIR      = DATA_DIR / "raw"                  / SPACE_KEY
RAW_HTML_DIR = DATA_DIR / "raw_html"             / SPACE_KEY
IMAGES_DIR   = DATA_DIR / "images"               / SPACE_KEY
PAGES_MD_DIR = DATA_DIR / "docupedia_data_page"  / SPACE_KEY
CHROMA_DIR   = DATA_DIR / "chroma_db"  # shared — all spaces in one vector store

# Create data directories on first import if they don't exist.
for _dir in [RAW_DIR, RAW_HTML_DIR, IMAGES_DIR, CHROMA_DIR, PAGES_MD_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Crawl settings
# ---------------------------------------------------------------------------
MAX_PAGES: int = int(os.getenv("MAX_PAGES", "0"))  # 0 = no limit
REQUEST_RETRIES: int = 3
REQUEST_TIMEOUT: int = 30
REQUEST_DELAY: float = 0.5

# ---------------------------------------------------------------------------
# Embedding settings
# ---------------------------------------------------------------------------
# EMBEDDING_MODEL can be a HuggingFace model ID (requires internet on first run)
# or a local directory path relative to PROJECT_ROOT (for offline machines).
# Example .env entry for offline use: EMBEDDING_MODEL=data/models/multilingual-e5-base
_model_env: str = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-base").strip()
# Resolve to an absolute path when the value points to an existing local directory.
_model_local = PROJECT_ROOT / _model_env
EMBEDDING_MODEL: str = str(_model_local) if _model_local.is_dir() else _model_env
EMBEDDING_DIMENSION: int = 768
CHROMA_COLLECTION_NAME: str = "docupedia"

# ---------------------------------------------------------------------------
# Chunking settings
# ---------------------------------------------------------------------------
CHUNK_SIZE: int = 512        # tokens per chunk
CHUNK_OVERLAP: int = 64      # token overlap between adjacent chunks
