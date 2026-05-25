"""
config.py — Central configuration for the Docupedia RAG pipeline.
Loads from .env and provides constants to all other modules.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"


def _parse_space_targets(raw_value: str) -> tuple[str, ...]:
    """Parse a comma-separated space list into a de-duplicated tuple."""
    targets: list[str] = []
    for part in raw_value.split(","):
        space_key = part.strip()
        if space_key and space_key not in targets:
            targets.append(space_key)
    return tuple(targets)

# ---------------------------------------------------------------------------
# Environment — loaded first so SPACE_KEY is available for path construction
# ---------------------------------------------------------------------------
load_dotenv(PROJECT_ROOT / ".env")

DOCUPEDIA_PAT: str = os.environ["DOCUPEDIA_PAT"]
DOCUPEDIA_BASE_URL: str = os.environ["DOCUPEDIA_BASE_URL"].rstrip("/")
SPACE_KEY: str = os.environ["SPACE_KEY"].strip()

# Optional: search scope for MCP/chat retrieval.
# Leave empty to search all indexed spaces, or set one or more comma-separated
# space keys such as "BBMRL,BBMDATAARCHITECTURE".
SPACE_TARGETS: tuple[str, ...] = _parse_space_targets(os.getenv("SPACE_TARGET", ""))

# Optional: crawl from a specific root page and all its descendants.
# Set to a Confluence page ID (integer string) to crawl that subtree only.
# Leave empty to crawl the entire space.
_page_id_raw: str = os.getenv("PAGE_ID", "").strip()
PAGE_ID: int | None = int(_page_id_raw) if _page_id_raw else None

# ---------------------------------------------------------------------------
# Paths — all crawl output is scoped under data/<type>/<SPACE_KEY>/
# CHROMA_DIR is shared across all spaces (one searchable vector store).
# ---------------------------------------------------------------------------
RAW_ROOT_DIR      = DATA_DIR / "raw"
RAW_HTML_ROOT_DIR = DATA_DIR / "raw_html"
IMAGES_ROOT_DIR   = DATA_DIR / "images"
PAGES_MD_ROOT_DIR = DATA_DIR / "docupedia_data_page"

RAW_DIR      = RAW_ROOT_DIR      / SPACE_KEY
RAW_HTML_DIR = RAW_HTML_ROOT_DIR / SPACE_KEY
IMAGES_DIR   = IMAGES_ROOT_DIR   / SPACE_KEY
PAGES_MD_DIR = PAGES_MD_ROOT_DIR / SPACE_KEY
CHROMA_DIR   = DATA_DIR / "chroma_db"  # shared — all spaces in one vector store

# Create data directories on first import if they don't exist.
for _dir in [
    RAW_ROOT_DIR,
    RAW_HTML_ROOT_DIR,
    IMAGES_ROOT_DIR,
    PAGES_MD_ROOT_DIR,
    RAW_DIR,
    RAW_HTML_DIR,
    IMAGES_DIR,
    CHROMA_DIR,
    PAGES_MD_DIR,
]:
    _dir.mkdir(parents=True, exist_ok=True)


def get_indexed_space_keys() -> tuple[str, ...]:
    """Return all locally available crawl spaces discovered under data/raw/."""
    return tuple(
        path.name
        for path in sorted(RAW_ROOT_DIR.iterdir(), key=lambda item: item.name.lower())
        if path.is_dir()
    )


def get_search_raw_dirs() -> tuple[Path, ...]:
    """Return the raw JSON directories that should be visible to search/page tools."""
    if SPACE_TARGETS:
        return tuple(
            path
            for path in (RAW_ROOT_DIR / space_key for space_key in SPACE_TARGETS)
            if path.is_dir()
        )

    return tuple(
        path
        for path in sorted(RAW_ROOT_DIR.iterdir(), key=lambda item: item.name.lower())
        if path.is_dir()
    )


def get_search_scope_label() -> str:
    """Return a human-readable description of the active search scope."""
    if SPACE_TARGETS:
        return ", ".join(SPACE_TARGETS)
    return "all indexed spaces"

# ---------------------------------------------------------------------------
# Crawl settings
# ---------------------------------------------------------------------------
MAX_PAGES: int        = int(os.getenv("MAX_PAGES", "0"))            # 0 = no limit
REQUEST_RETRIES: int  = 3
REQUEST_TIMEOUT: int  = 30
REQUEST_DELAY: float  = float(os.getenv("REQUEST_DELAY", "0.2"))    # seconds between API batches
CRAWL_BATCH_SIZE: int = int(os.getenv("CRAWL_BATCH_SIZE", "100"))   # pages per API request (max 100)
CRAWL_WORKERS: int    = int(os.getenv("CRAWL_WORKERS", "4"))        # parallel per-page processing threads

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

# ---------------------------------------------------------------------------
# OCR / Image-indexing settings
# ---------------------------------------------------------------------------
# Master switch for the OCR pipeline stage. When False, `python pipeline.py ocr`
# becomes a no-op and `run` skips the OCR step.
OCR_ENABLED: bool = os.getenv("OCR_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}

# Tesseract language pack(s). Multiple languages can be combined with '+',
# e.g. "eng+deu". Default targets English with German fallback.
OCR_LANGS: str = os.getenv("OCR_LANGS", "eng+deu").strip() or "eng+deu"

# Optional explicit path to the Tesseract binary (Windows installers do not
# always add it to PATH). Leave empty to use the system PATH.
TESSERACT_CMD: str = os.getenv("TESSERACT_CMD", "").strip()

# Parallel OCR worker threads per page. Tesseract releases the GIL, so a small
# pool helps; oversubscribing hurts because each instance is CPU-bound.
OCR_WORKERS: int = int(os.getenv("OCR_WORKERS", "2"))

# Filter thresholds — drop images cheaply before opening the file when possible.
OCR_MIN_WIDTH: int  = int(os.getenv("OCR_MIN_WIDTH",  "64"))
OCR_MIN_HEIGHT: int = int(os.getenv("OCR_MIN_HEIGHT", "64"))
OCR_MIN_BYTES: int  = int(os.getenv("OCR_MIN_BYTES",  "2048"))
OCR_MAX_PIXELS: int = int(os.getenv("OCR_MAX_PIXELS", "30000000"))   # 30 MP
OCR_MAX_RATIO: float = float(os.getenv("OCR_MAX_RATIO", "30.0"))      # extreme aspect ratio guard
OCR_PER_PAGE_LIMIT: int = int(os.getenv("OCR_PER_PAGE_LIMIT", "30"))

# Comma-separated filename substrings that mark an image as decorative.
# Matching is case-insensitive and substring-based.
_DEFAULT_OCR_BLOCKLIST = "externallink,icon_,spacer,bullet_,arrow_,loading,divider"
OCR_BLOCKLIST_PATTERNS: tuple[str, ...] = tuple(
    pat.strip().lower()
    for pat in os.getenv("OCR_BLOCKLIST_PATTERNS", _DEFAULT_OCR_BLOCKLIST).split(",")
    if pat.strip()
)

# Routing thresholds used to decide ocr-good / ocr-partial / ocr-empty.
OCR_GOOD_WORDS: int   = int(os.getenv("OCR_GOOD_WORDS", "20"))
OCR_GOOD_CONF: float  = float(os.getenv("OCR_GOOD_CONF", "70"))
OCR_PARTIAL_WORDS: int  = int(os.getenv("OCR_PARTIAL_WORDS", "5"))
OCR_PARTIAL_CONF: float = float(os.getenv("OCR_PARTIAL_CONF", "40"))
