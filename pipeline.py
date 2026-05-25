"""
pipeline.py — Entry point of the Docupedia RAG pipeline.

Commands:
  python pipeline.py crawl              → Crawl all pages: fetch HTML from Confluence,
                                          download attachments, save offline HTML + JSON + Markdown
  python pipeline.py crawl --limit 5   → Test with 5 pages
  python pipeline.py ocr                → Build images_index + OCR (between crawl and embed)
  python pipeline.py ocr --force        → Re-OCR even if ocr_text already exists
  python pipeline.py embed              → Read saved JSON, chunk and embed into ChromaDB
  python pipeline.py run                → crawl + ocr + embed in one shot
  python pipeline.py run --no-ocr       → crawl + embed only (skip OCR)

Authentication:
  PAT (Personal Access Token) is read from .env and sent automatically as a
  Bearer token on every Confluence API request. No explicit login step needed.

Data layout:
  data/raw_html/<pageid>.html                    → Raw offline HTML (attachment URLs rewritten to local)
  data/raw/<pageid>_<title>.json                 → Structured JSON (sections + images_index + metadata)
  data/images/<pageid>/                          → Downloaded page attachments
  docupedia_data_page/<pageid>_<title>.md → Markdown export
  data/chroma_db/                         → ChromaDB vector store
"""

import argparse
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

from tqdm import tqdm

# Đảm bảo root project trong sys.path
sys.path.insert(0, str(Path(__file__).parent))

import config
from crawler.confluence_client import ConfluenceClient
from crawler.page_parser import parse_page
from crawler.image_downloader import download_page_images
from processor.markdown_writer import write_page_markdown
from processor.chunker import chunk_page
from processor.ocr import run_ocr_for_page
from embedder.chroma_store import (
    get_collection_stats,
    has_indexed_space,
    update_chunk_metadatas,
    upsert_chunks,
)


def _safe_title(title: str) -> str:
    """Sanitize a page title for use in a filename (same rules as markdown_writer)."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)[:100]


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Step 1: Crawl
# ---------------------------------------------------------------------------

def run_crawl(limit: int = 0, page_id: int | None = None) -> None:
    """
    Full crawl pipeline per page:
      1. Fetch pages — either the full space (default) or a root page + all
         its descendants when page_id is given (via PAGE_ID env var or --page-id).
      2. Download all attachments and rewrite HTML URLs to local file paths
      3. Save offline HTML  → data/raw_html/<SPACE_KEY>/<pageid>.html
      4. Parse offline HTML → structured sections dict
      5. Save JSON          → data/raw/<SPACE_KEY>/<pageid>_<title>.json
      6. Write Markdown     → data/docupedia_data_page/<SPACE_KEY>/<pageid>_<title>.md

    Resume-safe: pages whose JSON already exists are skipped.

    Args:
        limit:   Max pages to crawl (0 = use MAX_PAGES from .env; 0 = all).
        page_id: Root page ID to crawl (None = crawl entire space).
    """
    if limit > 0:
        config.MAX_PAGES = limit

    # Resolve which page_id to use: CLI arg > .env PAGE_ID > None (full space)
    root_page_id: int | None = page_id or config.PAGE_ID

    logger.info("=== STEP 1: CRAWL ===")
    logger.info(f"Base URL     : {config.DOCUPEDIA_BASE_URL}")
    logger.info(f"Space Key    : {config.SPACE_KEY}")
    if root_page_id:
        logger.info(f"Root Page ID : {root_page_id} (subtree crawl)")
    else:
        logger.info(f"Scope        : full space")
    logger.info(f"MAX_PAGES    : {config.MAX_PAGES if config.MAX_PAGES > 0 else 'unlimited'}")
    logger.info(f"Workers      : {config.CRAWL_WORKERS}")
    logger.info(f"Batch size   : {config.CRAWL_BATCH_SIZE}")
    logger.info(f"Raw HTML out : {config.RAW_HTML_DIR}")
    logger.info(f"JSON out     : {config.RAW_DIR}")
    logger.info(f"Markdown out : {config.PAGES_MD_DIR}")

    client = ConfluenceClient()

    # Thread-safe counters (mutate dict in place — no nonlocal needed)
    _lock = Lock()
    _stats = {"success": 0, "failed": 0, "skipped": 0}

    def _process_page(page_meta: dict) -> str:
        """Download images, parse, save JSON + Markdown for one page."""
        pid = page_meta["pageid"]
        title = page_meta["title"]
        raw_output_path = config.RAW_DIR / f"{pid}_{_safe_title(title)}.json"

        # Delta check: skip only if the page is unchanged since the last crawl
        if raw_output_path.exists():
            try:
                with open(raw_output_path, encoding="utf-8") as f:
                    stored = json.load(f)
                if stored.get("last_modified") == page_meta.get("last_modified"):
                    with _lock:
                        _stats["skipped"] += 1
                    return "skip"
            except Exception:
                pass  # corrupted JSON or missing key — re-crawl

        try:
            raw_html: str = page_meta["html"]

            # ── Download attachments + rewrite HTML URLs to local paths ──
            offline_html, image_results = download_page_images(client, pid, raw_html)

            raw_html_path = config.RAW_HTML_DIR / f"{pid}.html"
            raw_html_path.write_text(offline_html, encoding="utf-8")

            page_for_parse = {**page_meta, "html": offline_html}
            parsed_page = parse_page(page_for_parse)
            parsed_page["downloaded_images"] = [
                r for r in image_results if not r["skipped"]
            ]

            with open(raw_output_path, "w", encoding="utf-8") as f:
                json.dump(parsed_page, f, ensure_ascii=False, indent=2)

            write_page_markdown(parsed_page)

            with _lock:
                _stats["success"] += 1
            logger.debug(f"OK: [{pid}] {title}")
            return "ok"

        except Exception as exc:
            logger.error(f"Error crawling [{pid}] {title}: {exc}")
            with _lock:
                _stats["failed"] += 1
            return "fail"

    page_source = (
        client.iter_pages_from_root(root_page_id)
        if root_page_id
        else client.iter_all_pages()
    )

    # Stream pages from the API and submit to the worker pool as they arrive.
    # Workers start processing immediately while the API is still being fetched.
    futures: list = []
    with ThreadPoolExecutor(max_workers=config.CRAWL_WORKERS) as pool:
        with tqdm(desc="Fetching pages", unit="page", dynamic_ncols=True) as pbar_fetch:
            for page_meta in page_source:
                futures.append(pool.submit(_process_page, page_meta))
                pbar_fetch.update(1)

        # All pages submitted; total is now known, so tqdm can show ETA
        for _f in tqdm(
            as_completed(futures),
            total=len(futures),
            desc="Processing  ",
            unit="page",
            dynamic_ncols=True,
        ):
            try:
                _f.result()
            except Exception as exc:
                logger.error(f"Worker error: {exc}")

    logger.info("=== CRAWL RESULT ===")
    logger.info(f"  Success  : {_stats['success']}")
    logger.info(f"  Failed   : {_stats['failed']}")
    logger.info(f"  Skipped  : {_stats['skipped']} (unchanged since last crawl)")
    logger.info(f"  HTML     : {config.RAW_HTML_DIR}")
    logger.info(f"  JSON     : {config.RAW_DIR}")
    logger.info(f"  Markdown : {config.PAGES_MD_DIR}")


# ---------------------------------------------------------------------------
# Step 1.5: OCR — populate images_index in saved JSON
# ---------------------------------------------------------------------------

def run_ocr(page_id: int | None = None, limit: int = 0, force: bool = False) -> None:
    """
    For each page JSON under ``data/raw/<SPACE_KEY>/``, build the
    ``images_index`` from the offline HTML and run tesseract OCR on indexable
    images. Updates each JSON file in place.

    Idempotent: by default we skip images that already have ``ocr_text``.
    Pass ``force=True`` to re-OCR everything.
    """
    if not config.OCR_ENABLED:
        logger.info("OCR_ENABLED=false in .env — skipping OCR stage.")
        return

    logger.info("=== STEP 1.5: OCR ===")
    logger.info(f"Space Key   : {config.SPACE_KEY}")
    logger.info(f"JSON dir    : {config.RAW_DIR}")
    logger.info(f"HTML dir    : {config.RAW_HTML_DIR}")
    logger.info(f"Languages   : {config.OCR_LANGS}")
    logger.info(f"Workers     : {config.OCR_WORKERS}")
    logger.info(f"Force re-OCR: {force}")

    json_files = sorted(config.RAW_DIR.glob("*.json"))
    if page_id is not None:
        matches = [p for p in json_files if p.stem.startswith(f"{page_id}_")]
        json_files = matches
    if limit > 0:
        json_files = json_files[:limit]

    if not json_files:
        logger.warning("No matching JSON files found — run 'crawl' first.")
        return

    totals = {
        "pages": 0,
        "total_images": 0,
        "skipped_filter": 0,
        "ocr_good": 0,
        "ocr_partial": 0,
        "ocr_empty": 0,
        "ocr_error": 0,
        "skipped_existing": 0,
    }

    for json_file in tqdm(json_files, desc="OCR", unit="page"):
        try:
            with open(json_file, encoding="utf-8") as f:
                parsed_page = json.load(f)

            images_index, summary = run_ocr_for_page(parsed_page, force=force)
            parsed_page["images_index"] = images_index

            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(parsed_page, f, ensure_ascii=False, indent=2)

            totals["pages"] += 1
            for key in (
                "total", "skipped_filter", "ocr_good", "ocr_partial",
                "ocr_empty", "ocr_error", "skipped_existing",
            ):
                totals_key = "total_images" if key == "total" else key
                totals[totals_key] += summary.get(key, 0)

        except Exception as exc:
            logger.error(f"OCR error {json_file.name}: {exc}")

    logger.info("=== OCR RESULT ===")
    logger.info(f"  Pages processed   : {totals['pages']}")
    logger.info(f"  Images total      : {totals['total_images']}")
    logger.info(f"  Skipped by filter : {totals['skipped_filter']}")
    logger.info(f"  Skipped existing  : {totals['skipped_existing']}")
    logger.info(f"  OCR good          : {totals['ocr_good']}")
    logger.info(f"  OCR partial       : {totals['ocr_partial']}")
    logger.info(f"  OCR empty         : {totals['ocr_empty']}")
    logger.info(f"  OCR error         : {totals['ocr_error']}")


# ---------------------------------------------------------------------------
# Step 2: Embed
# ---------------------------------------------------------------------------

def run_embed() -> None:
    """
    Read all JSON files from data/raw/, chunk each page into text segments,
    embed with sentence-transformers, and upsert into ChromaDB.

    Idempotent: ChromaDB uses upsert, so re-running does not duplicate data.
    """
    logger.info("=== STEP 2: EMBED ===")
    logger.info(f"Embedding model : {config.EMBEDDING_MODEL}")
    logger.info(f"ChromaDB dir    : {config.CHROMA_DIR}")

    json_files = sorted(config.RAW_DIR.glob("*.json"))
    if not json_files:
        logger.warning(f"No JSON files found in {config.RAW_DIR}. Run 'crawl' first.")
        return

    logger.info(f"Pages to embed: {len(json_files)}")

    success = 0
    failed = 0

    for json_file in tqdm(json_files, desc="Embedding", unit="page"):
        try:
            with open(json_file, encoding="utf-8") as f:
                parsed_page = json.load(f)

            chunks = chunk_page(parsed_page)
            if chunks:
                upsert_chunks(chunks)
            success += 1

        except Exception as exc:
            logger.error(f"Embed error {json_file.name}: {exc}")
            failed += 1

    stats = get_collection_stats()
    logger.info("=== EMBED RESULT ===")
    logger.info(f"  Success         : {success}")
    logger.info(f"  Failed          : {failed}")
    logger.info(f"  ChromaDB chunks : {stats['count']}")


def run_sync_metadata() -> None:
    """
    Update chunk metadata in ChromaDB without recomputing embeddings.

    Useful after metadata schema changes, for example when adding ``space_key``
    to existing chunks so search can be restricted to the active Docupedia space.
    """
    logger.info("=== STEP 2: SYNC METADATA ===")
    logger.info(f"Space Key       : {config.SPACE_KEY}")
    logger.info(f"JSON source dir : {config.RAW_DIR}")
    logger.info(f"ChromaDB dir    : {config.CHROMA_DIR}")

    json_files = sorted(config.RAW_DIR.glob("*.json"))
    if not json_files:
        logger.warning(f"No JSON files found in {config.RAW_DIR}. Run 'crawl' first.")
        return

    logger.info(f"Pages to sync: {len(json_files)}")

    success = 0
    failed = 0

    for json_file in tqdm(json_files, desc="Sync metadata", unit="page"):
        try:
            with open(json_file, encoding="utf-8") as f:
                parsed_page = json.load(f)

            chunks = chunk_page(parsed_page)
            if chunks:
                update_chunk_metadatas(chunks)
            success += 1

        except Exception as exc:
            logger.error(f"Metadata sync error {json_file.name}: {exc}")
            failed += 1

    logger.info("=== METADATA SYNC RESULT ===")
    logger.info(f"  Success             : {success}")
    logger.info(f"  Failed              : {failed}")
    logger.info(f"  Space filter active : {has_indexed_space(config.SPACE_KEY)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Docupedia RAG Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline.py crawl                        # Crawl entire space
  python pipeline.py crawl --limit 5             # Test with 5 pages
  python pipeline.py crawl --page-id 2155921768  # Crawl a page subtree
  python pipeline.py ocr                          # Build images_index + run OCR
  python pipeline.py ocr --page-id 2155921768    # Re-OCR a single page
  python pipeline.py ocr --force                  # Re-OCR everything
  python pipeline.py embed                        # Embed JSON → ChromaDB
  python pipeline.py run                          # Crawl + OCR + Embed (full pipeline)
  python pipeline.py run --no-ocr                # Crawl + Embed (skip OCR)
  python pipeline.py run --page-id 2155921768    # Subtree crawl + ocr + embed
""",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── crawl ──────────────────────────────────────────────────────────────
    crawl_parser = subparsers.add_parser("crawl", help="Crawl Confluence space or page subtree")
    crawl_parser.add_argument(
        "--limit", type=int, default=0,
        help="Max pages to crawl (0 = no limit / use MAX_PAGES from .env)",
    )
    crawl_parser.add_argument(
        "--page-id", type=int, default=None, dest="page_id",
        help="Root page ID — crawl this page and all its sub-pages (overrides PAGE_ID in .env)",
    )

    # ── embed ──────────────────────────────────────────────────────────────
    subparsers.add_parser("embed", help="Chunk + embed saved JSON into ChromaDB")

    # ── ocr ────────────────────────────────────────────────────────────────
    ocr_parser = subparsers.add_parser(
        "ocr",
        help="Build images_index and run OCR on saved pages (between crawl and embed)",
    )
    ocr_parser.add_argument(
        "--limit", type=int, default=0,
        help="Max pages to OCR (0 = no limit)",
    )
    ocr_parser.add_argument(
        "--page-id", type=int, default=None, dest="page_id",
        help="Only OCR the single page with this Confluence ID",
    )
    ocr_parser.add_argument(
        "--force", action="store_true",
        help="Re-OCR images that already have ocr_text",
    )

    # ── sync-metadata ──────────────────────────────────────────────────────
    subparsers.add_parser(
        "sync-metadata",
        help="Update Chroma metadata from saved JSON without recomputing embeddings",
    )

    # ── run (all) ──────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser("run", help="Full pipeline: crawl → ocr → embed")
    run_parser.add_argument(
        "--limit", type=int, default=0,
        help="Max pages to crawl (0 = no limit)",
    )
    run_parser.add_argument(
        "--page-id", type=int, default=None, dest="page_id",
        help="Root page ID — crawl this page and all its sub-pages (overrides PAGE_ID in .env)",
    )
    run_parser.add_argument(
        "--no-ocr", action="store_true",
        help="Skip the OCR stage even when OCR_ENABLED=true",
    )

    args = parser.parse_args()

    if args.command == "crawl":
        run_crawl(limit=args.limit, page_id=args.page_id)
    elif args.command == "embed":
        run_embed()
    elif args.command == "ocr":
        run_ocr(page_id=args.page_id, limit=args.limit, force=args.force)
    elif args.command == "sync-metadata":
        run_sync_metadata()
    elif args.command == "run":
        run_crawl(limit=getattr(args, "limit", 0), page_id=getattr(args, "page_id", None))
        if not getattr(args, "no_ocr", False):
            run_ocr(page_id=getattr(args, "page_id", None))
        run_embed()


if __name__ == "__main__":
    main()
