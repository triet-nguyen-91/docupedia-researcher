"""
mcp_server/server.py — FastMCP server exposing Docupedia RAG tools to GitHub Copilot.

Tools exposed:
  search_docs(query, n_results)  → semantic search over ChromaDB
  get_page(page_id)              → full structured page from data/raw/
  list_pages()                   → all page titles/IDs known in the local store

Transport: stdio — VS Code spawns this process automatically via .vscode/mcp.json.

Usage (manual):
  python -m mcp_server.server
"""

import json
import sys
from pathlib import Path

# Ensure project root is on sys.path so config and embedder are importable.
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

import config
from embedder.chroma_store import query as chroma_query, get_collection_stats

mcp = FastMCP("docupedia")


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
    results = chroma_query(query, n_results=n_results)

    if not results:
        return (
            "No results found in the knowledge base. "
            "Make sure the pipeline has been run: `python pipeline.py run`."
        )

    blocks = []
    for i, r in enumerate(results, 1):
        meta = r["metadata"]
        title = meta.get("title", "Unknown page")
        section = meta.get("section", "")
        url = meta.get("url", "")
        distance = r["distance"]
        text = r["text"]

        heading = f"{title}" + (f" — {section}" if section and section != title else "")
        blocks.append(
            f"### Result {i}: {heading}\n"
            f"URL: {url}\n"
            f"Relevance: {distance:.4f} (lower = closer match)\n\n"
            f"{text}"
        )

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
    matches = list(config.RAW_DIR.glob(f"{page_id}_*.json"))
    if not matches:
        return (
            f"Page {page_id} not found in local store.\n"
            "Use list_pages() to see available pages, or re-run the crawl."
        )

    with open(matches[0], encoding="utf-8") as f:
        page = json.load(f)

    title = page.get("title", "Unknown")
    url = page.get("url", "")
    last_modified = page.get("last_modified", "")

    lines = [
        f"# {title}",
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
    json_files = sorted(config.RAW_DIR.glob("*.json"))
    if not json_files:
        return (
            "No pages found in local store.\n"
            "Run `python pipeline.py crawl` to populate it."
        )

    pages = []
    for jf in json_files:
        try:
            with open(jf, encoding="utf-8") as f:
                data = json.load(f)
            pages.append({
                "page_id": data.get("pageid", "?"),
                "title": data.get("title", jf.stem),
                "url": data.get("url", ""),
            })
        except Exception:
            continue

    pages.sort(key=lambda p: str(p["title"]).lower())

    try:
        stats = get_collection_stats()
        chunk_count = stats["count"]
    except Exception:
        chunk_count = "unknown (ChromaDB not yet initialised)"

    lines = [
        f"Pages in local store : {len(pages)}",
        f"Chunks in ChromaDB   : {chunk_count}",
        "",
    ]
    for p in pages:
        lines.append(f"- [{p['page_id']}] {p['title']}  →  {p['url']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
