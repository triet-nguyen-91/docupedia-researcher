"""
pipeline.py — Entry point of the Docupedia RAG pipeline.

Commands:
  python pipeline.py crawl              → Crawl all pages: fetch HTML from Confluence,
                                          download attachments, save offline HTML + JSON + Markdown
  python pipeline.py crawl --limit 5   → Test with 5 pages
  python pipeline.py embed              → Read saved JSON, chunk and embed into ChromaDB
  python pipeline.py run                → crawl + embed in one shot

Authentication:
  PAT (Personal Access Token) is read from .env and sent automatically as a
  Bearer token on every Confluence API request. No explicit login step needed.

Data layout:
  data/raw_html/<pageid>.html                    → Raw offline HTML (attachment URLs rewritten to local)
  data/raw/<pageid>_<title>.json                 → Structured JSON (sections + metadata)
  data/images/<pageid>/                          → Downloaded page attachments
  docupedia_data_page/<pageid>_<title>.md → Markdown export
  data/chroma_db/                         → ChromaDB vector store
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from tqdm import tqdm

# Đảm bảo root project trong sys.path
sys.path.insert(0, str(Path(__file__).parent))

import config
from crawler.confluence_client import ConfluenceClient
from crawler.page_parser import parse_page
from crawler.image_downloader import download_page_images
from processor.markdown_writer import write_page_markdown
from processor.chunker import chunk_page
from embedder.chroma_store import upsert_chunks, get_collection_stats


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

def run_crawl(limit: int = 0) -> None:
    """
    Full crawl pipeline per page:
      1. Fetch all pages from Confluence space (HTML body included in batch response)
      2. Download all attachments and rewrite HTML URLs to local file paths
      3. Save offline HTML  → data/raw_html/<pageid>.html  (raw data)
      4. Parse offline HTML → structured sections dict
      5. Save JSON          → data/raw/<pageid>.json
      6. Write Markdown     → docupedia_data_page/<pageid>_<title>.md

    Resume-safe: pages whose JSON already exists are skipped.

    Args:
        limit: Max pages to crawl (0 = use MAX_PAGES from .env; 0 = all).
    """
    if limit > 0:
        config.MAX_PAGES = limit

    logger.info("=== STEP 1: CRAWL ===")
    logger.info(f"Base URL     : {config.DOCUPEDIA_BASE_URL}")
    logger.info(f"Space Key    : {config.SPACE_KEY}")
    logger.info(f"MAX_PAGES    : {config.MAX_PAGES if config.MAX_PAGES > 0 else 'unlimited'}")
    logger.info(f"Raw HTML out : {config.RAW_HTML_DIR}")
    logger.info(f"JSON out     : {config.RAW_DIR}")
    logger.info(f"Markdown out : {config.PAGES_MD_DIR}")

    # PAT authentication is automatic — token is set on the session at construction.
    client = ConfluenceClient()

    logger.info("Fetching page list from Confluence...")
    all_pages = list(client.iter_all_pages())
    logger.info(f"Total pages to crawl: {len(all_pages)}")

    success = 0
    failed = 0
    skipped = 0

    for page_meta in tqdm(all_pages, desc="Crawling", unit="page"):
        page_id = page_meta["pageid"]
        title = page_meta["title"]
        raw_output_path = config.RAW_DIR / f"{page_id}_{_safe_title(title)}.json"

        if raw_output_path.exists():
            logger.debug(f"Skip (already done): {title}")
            skipped += 1
            continue

        try:
            raw_html: str = page_meta["html"]

            # ── Download attachments + rewrite HTML URLs to local paths ──
            offline_html, image_results = download_page_images(client, page_id, raw_html)

            # ── Save raw offline HTML ─────────────────────────────────────
            raw_html_path = config.RAW_HTML_DIR / f"{page_id}.html"
            raw_html_path.write_text(offline_html, encoding="utf-8")

            # ── Parse offline HTML → sections ─────────────────────────────
            page_for_parse = {**page_meta, "html": offline_html}
            parsed_page = parse_page(page_for_parse)
            parsed_page["downloaded_images"] = [
                r for r in image_results if not r["skipped"]
            ]

            # ── Save JSON ─────────────────────────────────────────────────
            with open(raw_output_path, "w", encoding="utf-8") as f:
                json.dump(parsed_page, f, ensure_ascii=False, indent=2)

            # ── Write Markdown ────────────────────────────────────────────
            write_page_markdown(parsed_page)

            success += 1
            logger.debug(f"OK: [{page_id}] {title}")

        except Exception as exc:
            logger.error(f"Error crawling [{page_id}] {title}: {exc}")
            failed += 1

    logger.info("=== CRAWL RESULT ===")
    logger.info(f"  Success  : {success}")
    logger.info(f"  Failed   : {failed}")
    logger.info(f"  Skipped  : {skipped} (already processed)")
    logger.info(f"  HTML     : {config.RAW_HTML_DIR}")
    logger.info(f"  JSON     : {config.RAW_DIR}")
    logger.info(f"  Markdown : {config.PAGES_MD_DIR}")


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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Docupedia RAG Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
  python pipeline.py crawl               # Crawl toàn bộ → JSON + Markdown
  python pipeline.py crawl --limit 5    # Test với 5 trang
  python pipeline.py embed               # Embed JSON → ChromaDB
  python pipeline.py run                 # Crawl + Embed (toàn bộ pipeline)
""",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── crawl ──────────────────────────────────────────────────────────────
    crawl_parser = subparsers.add_parser(
        "crawl", help="Crawl Docupedia → lưu raw JSON + Markdown"
    )
    crawl_parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Giới hạn số trang (0 = dùng MAX_PAGES từ .env, 0 nghĩa là toàn bộ)",
    )

    # ── embed ──────────────────────────────────────────────────────────────
    subparsers.add_parser(
        "embed", help="Đọc JSON đã crawl, embed và lưu vào ChromaDB"
    )

    # ── run (all) ──────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser(
        "run", help="Chạy toàn bộ pipeline: crawl → embed"
    )
    run_parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Giới hạn số trang khi crawl (0 = toàn bộ)",
    )

    args = parser.parse_args()

    if args.command == "crawl":
        run_crawl(limit=args.limit)
    elif args.command == "embed":
        run_embed()
    elif args.command == "run":
        run_crawl(limit=getattr(args, "limit", 0))
        run_embed()


if __name__ == "__main__":
    main()
