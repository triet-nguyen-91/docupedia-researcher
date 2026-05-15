"""
crawler/page_parser.py

Parse Confluence HTML body into structured sections/headings.
Uses BeautifulSoup to split content at heading boundaries and markdownify
to convert each section's HTML to clean Markdown text.

Output structure per page:
{
    "pageid": int,
    "title": str,
    "url": str,
    "last_modified": str,
    "categories": list[str],
    "images": list[str],
    "sections": [
        {
            "heading": str | None,   # None for intro section (before first heading)
            "level": int,            # 1=H1, 2=H2, ... 0 for intro
            "text": str,             # Markdown text of section body
        },
        ...
    ]
}
"""

from __future__ import annotations

import re
import logging

from bs4 import BeautifulSoup
from markdownify import markdownify as md

logger = logging.getLogger(__name__)

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_page(raw_page: dict) -> dict:
    """
    Convert a raw page dict (from ConfluenceClient.iter_all_pages) into a
    structured page dict with a ``sections`` list.

    Args:
        raw_page: dict containing pageid, title, url, html, last_modified,
                  categories, images.

    Returns:
        dict with the same fields but html replaced by sections list.
    """
    html: str = raw_page.get("html", "")
    sections = _split_html_into_sections(html)

    return {
        "pageid": raw_page["pageid"],
        "title": raw_page["title"],
        "url": raw_page["url"],
        "last_modified": raw_page["last_modified"],
        "categories": raw_page["categories"],
        "images": raw_page.get("images", []),
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _split_html_into_sections(html: str) -> list[dict]:
    """
    Split Confluence HTML body into sections delimited by heading tags.

    Walks the top-level children of the parsed HTML.  When a heading tag
    (h1–h6) is encountered the current section is flushed and a new one
    starts.  Section body HTML is converted to Markdown via markdownify.

    Handles the common Confluence pattern where all content is wrapped in
    a single top-level <div> (e.g. <div class="wiki-content">): the wrapper
    is transparently unwrapped so headings are visible at the iteration level.

    Returns:
        list of {"heading": str|None, "level": int, "text": str}
    """
    soup = BeautifulSoup(html, "html.parser")

    # Unwrap a single top-level <div> wrapper that Confluence sometimes adds
    # (e.g. <div class="wiki-content group">…</div>).
    # Only unwrap when it is the sole real element; mixed content is left as-is.
    real_children = [el for el in soup.children if getattr(el, "name", None)]
    if len(real_children) == 1 and real_children[0].name == "div":
        root = real_children[0]
    else:
        root = soup

    sections: list[dict] = []

    current_heading: str | None = None
    current_level: int = 0
    current_html_parts: list[str] = []

    def flush() -> None:
        body = "".join(current_html_parts).strip()
        if not body and not current_heading:
            return
        text = _clean_text(md(body, heading_style="ATX")) if body else ""
        sections.append({
            "heading": current_heading,
            "level": current_level,
            "text": text,
        })

    for element in root.children:
        tag_name = getattr(element, "name", None)
        if tag_name in _HEADING_TAGS:
            flush()
            current_html_parts = []
            current_heading = element.get_text(strip=True)
            current_level = int(tag_name[1])  # "h2" → 2
        else:
            current_html_parts.append(str(element))

    flush()

    # Drop sections that have neither heading nor body text
    return [s for s in sections if s["heading"] or s["text"].strip()]


def _clean_text(text: str) -> str:
    """Collapse runs of 3+ blank lines down to two."""
    return re.sub(r"\n{3,}", "\n\n", text).strip()
