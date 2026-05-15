"""
crawler/confluence_client.py

Confluence-based Docupedia client with PAT (Personal Access Token) authentication.

Responsibilities:
  - Iterate all pages in the configured Space, returning full page data
    (HTML body, metadata, labels) in one paginated batch call — no separate
    per-page request needed.
  - Automatic retry with random back-off on network failures.

Authentication is handled automatically via the PAT Bearer token set on the
underlying requests.Session at construction time. No explicit login() call
is required.

Usage:
    client = ConfluenceClient()
    for page in client.iter_all_pages():
        html   = page["html"]
        labels = page["categories"]
"""

from __future__ import annotations

import logging
import random
import time
from typing import Iterator

import urllib3
from atlassian import Confluence
from requests.exceptions import RequestException

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)


class ConfluenceClient:
    """
    Wraps atlassian-python-api Confluence.
    Authenticates via PAT Bearer token.
    All page data (HTML body, version, labels) is fetched in one batch
    call using expand="body.view,version,metadata.labels".
    """

    def __init__(self) -> None:
        self.confluence = Confluence(
            url=config.DOCUPEDIA_BASE_URL,
            token=config.DOCUPEDIA_PAT,
            verify_ssl=False,
        )
        # PAT is sent as a Bearer token on every request automatically.
        # Disable system proxy so the session goes directly to the server.
        self.confluence.session.trust_env = False

    # ------------------------------------------------------------------
    # Page iteration — fetches HTML body inline (one request per batch)
    # ------------------------------------------------------------------

    def iter_all_pages(self) -> Iterator[dict]:
        """
        Yield every page in the configured space with full content.

        Mirrors the POC's safe_get_pages loop:
          - Batches of 20 pages with expand="body.view,version,metadata.labels"
          - Random delay 1-3 s between batches
          - Retries up to REQUEST_RETRIES times on RequestException

        Each yielded dict:
        {
            "pageid":        int,
            "title":         str,
            "html":          str,        # rendered body (body.view.value)
            "last_modified": str,        # ISO 8601 (version.when)
            "url":           str,        # full web URL
            "categories":    list[str],  # label names
        }
        Respects config.MAX_PAGES (0 = unlimited).
        """
        start = 0
        batch_size = 20
        total_yielded = 0

        while True:
            pages = self._fetch_batch(start, batch_size)
            if not pages:
                break

            for page in pages:
                labels = (
                    page.get("metadata", {})
                        .get("labels", {})
                        .get("results", [])
                )
                yield {
                    "pageid": int(page["id"]),
                    "title": page["title"],
                    "html": page.get("body", {}).get("view", {}).get("value", ""),
                    "last_modified": page.get("version", {}).get("when", ""),
                    "url": (
                        config.DOCUPEDIA_BASE_URL
                        + page.get("_links", {}).get("webui", f"/pages/{page['id']}")
                    ),
                    "categories": [lbl["name"] for lbl in labels],
                }
                total_yielded += 1

                if config.MAX_PAGES > 0 and total_yielded >= config.MAX_PAGES:
                    logger.info(f"Reached MAX_PAGES={config.MAX_PAGES}. Stopping.")
                    return

            if len(pages) < batch_size:
                break

            start += batch_size

            # Random delay between batches (mirrors POC pattern)
            delay = random.uniform(1.0, 3.0)
            logger.debug(f"Batch done, sleeping {delay:.2f}s before next batch.")
            time.sleep(delay)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_batch(self, start: int, limit: int) -> list[dict]:
        """Fetch one paginated batch with retry + back-off."""
        for attempt in range(config.REQUEST_RETRIES):
            try:
                return self.confluence.get_all_pages_from_space(
                    space=config.SPACE_KEY,
                    start=start,
                    limit=limit,
                    expand="body.view,version,metadata.labels",
                    content_type='page'
                ) or []
            except RequestException as exc:
                logger.warning(
                    f"Batch fetch failed at start={start} "
                    f"(attempt {attempt + 1}/{config.REQUEST_RETRIES}): {exc}"
                )
                time.sleep(2)
        raise RuntimeError(
            f"Failed to fetch pages at start={start} after {config.REQUEST_RETRIES} retries."
        )


