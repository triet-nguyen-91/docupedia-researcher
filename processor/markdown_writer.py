"""
processor/markdown_writer.py

Converts a parsed page dict (from page_parser.py) into a Markdown file
and saves it to config.PAGES_MD_DIR.

Markdown structure:
  - YAML frontmatter (pageid, title, url, last_modified, categories)
  - Page title as H1
  - Each section rendered with its heading level and body text
"""

import re
import logging
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import config

logger = logging.getLogger(__name__)

# Map wikitext heading levels to Markdown heading prefixes.
# Level 0 = intro (no heading); levels 2-6 follow MediaWiki convention.
_HEADING_PREFIX: dict[int, str] = {
    0: "",
    1: "#",
    2: "##",
    3: "###",
    4: "####",
    5: "#####",
    6: "######",
}


def write_page_markdown(parsed_page: dict) -> Path:
    """
    Write a parsed page to a ``.md`` file inside ``config.PAGES_MD_DIR``.

    Args:
        parsed_page: dict returned by ``crawler.page_parser.parse_page()``.

    Returns:
        Absolute Path of the written file.
    """
    pageid = parsed_page["pageid"]
    title = parsed_page["title"]
    safe_title = _sanitize_filename(title)
    output_path = config.PAGES_MD_DIR / f"{pageid}_{safe_title}.md"

    lines: list[str] = []

    # ── YAML frontmatter ──────────────────────────────────────────────────
    lines.append("---")
    lines.append(f"pageid: {pageid}")
    # Quote title/url to guard against special YAML characters
    lines.append(f'title: "{title.replace(chr(34), chr(39))}"')
    lines.append(f'url: "{parsed_page.get("url", "")}"')
    lines.append(f'last_modified: "{parsed_page.get("last_modified", "")}"')
    categories = parsed_page.get("categories", [])
    lines.append(f"categories: {categories}")
    lines.append("---")
    lines.append("")

    # ── Page title ────────────────────────────────────────────────────────
    lines.append(f"# {title}")
    lines.append("")

    # ── Sections ──────────────────────────────────────────────────────────
    for section in parsed_page.get("sections", []):
        heading = section.get("heading")
        level = section.get("level", 0)
        text = section.get("text", "").strip()

        if heading:
            prefix = _HEADING_PREFIX.get(level, "##")
            lines.append(f"{prefix} {heading}")
            lines.append("")

        if text:
            lines.append(text)
            lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.debug(f"Wrote markdown: {output_path.name}")
    return output_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_filename(name: str) -> str:
    """Replace characters that are invalid in file names with underscore."""
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return sanitized[:100]  # cap length to avoid OS limits
