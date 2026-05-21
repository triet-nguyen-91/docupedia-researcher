"""
mcp_server/server.py — FastMCP server exposing Docupedia RAG tools to GitHub Copilot.

Tools exposed:
    search_docs(query, n_results)  → semantic search over ChromaDB
    get_page(page_id)              → full structured page from data/raw/
    list_pages()                   → page titles/IDs known in the visible search scope

Transport: stdio — VS Code spawns this process automatically via .vscode/mcp.json.

Usage:
    python -m mcp_server.server                      # MCP stdio server for VS Code
    python -m mcp_server.server --healthcheck       # terminal-friendly status output
    python -m mcp_server.server --search "BBMRL"    # run one local search and exit
"""

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path so config and embedder are importable.
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

import config
from embedder.chroma_store import (
    get_collection_stats,
    get_missing_indexed_spaces,
    query as chroma_query,
)

mcp = FastMCP("docupedia")


def _iter_search_json_files() -> list[Path]:
    """Return all raw JSON files visible to the active search scope."""
    json_files: list[Path] = []
    for raw_dir in config.get_search_raw_dirs():
        json_files.extend(raw_dir.glob("*.json"))
    return sorted(json_files, key=lambda path: (path.parent.name.lower(), path.name.lower()))


def _find_page_file(page_id: int) -> Path | None:
    """Locate a page JSON file within the active search scope."""
    for raw_dir in config.get_search_raw_dirs():
        matches = sorted(raw_dir.glob(f"{page_id}_*.json"))
        if matches:
            return matches[0]
    return None


# ---------------------------------------------------------------------------
# Tool: search_docs
# ---------------------------------------------------------------------------

@mcp.tool()
def search_docs(query: str, n_results: int = 5) -> str:
    """
    Semantic search over the Docupedia knowledge base.

    Embeds the query with multilingual-e5-base and returns the top matching
    document chunks with their page title, section heading, source URL, and
    relevance distance. Supports queries in Vietnamese, English, or German.

    Args:
        query: Natural-language question or search phrase.
        n_results: Number of top results to return (default 5, max 20).
    """
    n_results = min(max(1, n_results), 20)
    requested_targets = config.SPACE_TARGETS or None
    missing_targets = get_missing_indexed_spaces(requested_targets)
    results = chroma_query(query, n_results=n_results, space_keys=requested_targets)

    if not results:
        scope_label = config.get_search_scope_label()
        missing_note = ""
        if missing_targets:
            missing_list = ", ".join(missing_targets)
            missing_note = (
                f" Requested spaces without searchable metadata: {missing_list}. "
                "Run `python pipeline.py sync-metadata` once with `SPACE_KEY` set to each of those spaces."
            )
        return (
            "No results found in the knowledge base. "
            f"Search scope: {scope_label}. "
            "Make sure the pipeline has been run: `python pipeline.py run`."
            f"{missing_note}"
        )

    blocks = []
    for i, r in enumerate(results, 1):
        meta = r["metadata"]
        title = meta.get("title", "Unknown page")
        space_key = meta.get("space_key", "")
        section = meta.get("section", "")
        url = meta.get("url", "")
        distance = r["distance"]
        text = r["text"]

        heading = f"{title}" + (f" — {section}" if section and section != title else "")
        block_lines = [f"### Result {i}: {heading}"]
        if space_key:
            block_lines.append(f"Space: {space_key}")
        block_lines.extend([
            f"URL: {url}",
            f"Relevance: {distance:.4f} (lower = closer match)",
            "",
            text,
        ])
        blocks.append("\n".join(block_lines))

    return "\n\n---\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Tool: get_page
# ---------------------------------------------------------------------------

@mcp.tool()
def get_page(page_id: int) -> str:
    """
    Retrieve the full structured content of a Docupedia page by its page ID.

    Returns the page title, URL, last modified date, and all section headings
    with their full text. Use list_pages() first to find page IDs.

    Args:
        page_id: Numeric Confluence page ID (e.g. 493982645).
    """
    match = _find_page_file(page_id)
    if match is None:
        return (
            f"Page {page_id} not found in local store.\n"
            f"Search scope: {config.get_search_scope_label()}.\n"
            "Use list_pages() to see available pages, or re-run the crawl."
        )

    with open(match, encoding="utf-8") as f:
        page = json.load(f)

    title = page.get("title", "Unknown")
    url = page.get("url", "")
    last_modified = page.get("last_modified", "")
    space_key = match.parent.name

    lines = [
        f"# {title}",
        f"Space: {space_key}",
        f"URL: {url}",
        f"Last modified: {last_modified}",
        "",
    ]
    for section in page.get("sections", []):
        heading = section.get("heading", "")
        text = section.get("text", "").strip()
        if heading:
            lines.append(f"## {heading}")
        if text:
            lines.append(text)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: list_pages
# ---------------------------------------------------------------------------

@mcp.tool()
def list_pages() -> str:
    """
    List all Docupedia pages currently stored in the local knowledge base.

    Returns page IDs and titles sorted alphabetically, plus total chunk count
    in ChromaDB. Use a page_id from this list with get_page() to fetch full
    content, or use it to scope a search_docs() query.
    """
    json_files = _iter_search_json_files()
    if not json_files:
        return (
            "No pages found in local store.\n"
            f"Search scope: {config.get_search_scope_label()}.\n"
            "Run `python pipeline.py crawl` to populate it."
        )

    pages = []
    for jf in json_files:
        try:
            with open(jf, encoding="utf-8") as f:
                data = json.load(f)
            pages.append({
                "space_key": jf.parent.name,
                "page_id": data.get("pageid", "?"),
                "title": data.get("title", jf.stem),
                "url": data.get("url", ""),
            })
        except Exception:
            continue

    pages.sort(key=lambda p: (str(p["title"]).lower(), str(p["space_key"]).lower()))

    try:
        stats = get_collection_stats()
        chunk_count = stats["count"]
    except Exception:
        chunk_count = "unknown (ChromaDB not yet initialised)"

    spaces_in_scope = sorted({p["space_key"] for p in pages})
    lines = [
        f"Search scope         : {config.get_search_scope_label()}",
        f"Spaces in local store : {len(spaces_in_scope)}",
        f"Pages in local store : {len(pages)}",
        f"Chunks in ChromaDB   : {chunk_count}",
        "",
    ]
    for p in pages:
        lines.append(f"- [{p['space_key']}] [{p['page_id']}] {p['title']}  →  {p['url']}")

    return "\n".join(lines)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Docupedia MCP server and local debugging CLI"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--healthcheck",
        action="store_true",
        help="print local space and Chroma status, then exit",
    )
    mode.add_argument(
        "--search",
        metavar="QUERY",
        help="run one local semantic search and print the results",
    )
    mode.add_argument(
        "--page-id",
        type=int,
        help="print one locally stored page by page ID",
    )
    mode.add_argument(
        "--list-pages",
        action="store_true",
        help="print the locally stored page list and exit",
    )
    parser.add_argument(
        "--n-results",
        type=int,
        default=5,
        help="number of results for --search (default: 5)",
    )
    return parser


def _print_healthcheck() -> None:
    try:
        chunk_count = get_collection_stats()["count"]
    except Exception:
        chunk_count = None

    search_raw_dirs = config.get_search_raw_dirs()
    missing_targets = get_missing_indexed_spaces(config.SPACE_TARGETS)
    payload = {
        "crawl_space_key": config.SPACE_KEY,
        "space_targets": list(config.SPACE_TARGETS),
        "search_scope": config.get_search_scope_label(),
        "search_raw_dirs": [str(path) for path in search_raw_dirs],
        "search_raw_pages": sum(len(list(path.glob("*.json"))) for path in search_raw_dirs),
        "chroma_dir": str(config.CHROMA_DIR),
        "chroma_chunks": chunk_count,
        "missing_space_metadata": list(missing_targets),
        "transport": "stdio",
    }
    print(json.dumps(payload, indent=2))


def main() -> None:
    args = _build_cli_parser().parse_args()

    if args.healthcheck:
        _print_healthcheck()
        return

    if args.search:
        print(search_docs(args.search, n_results=args.n_results))
        return

    if args.page_id is not None:
        print(get_page(args.page_id))
        return

    if args.list_pages:
        print(list_pages())
        return

    mcp.run(transport="stdio")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
