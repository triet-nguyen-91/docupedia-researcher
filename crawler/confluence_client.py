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
        # Explicitly add the PAT Bearer token to the session headers so that
        # both library methods AND direct session.get() calls are authenticated.
        self.confluence.session.headers.update({
            "Authorization": f"Bearer {config.DOCUPEDIA_PAT}"
        })
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
        batch_size = config.CRAWL_BATCH_SIZE
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

            time.sleep(config.REQUEST_DELAY)

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

    def _fetch_page(self, page_id: int) -> dict | None:
        """Fetch a single page by ID using get_page_by_id with retry + back-off."""
        for attempt in range(config.REQUEST_RETRIES):
            try:
                return self.confluence.get_page_by_id(
                    page_id=str(page_id),
                    expand="body.view,version,metadata.labels",
                )
            except RequestException as exc:
                logger.warning(
                    f"Page fetch failed for id={page_id} "
                    f"(attempt {attempt + 1}/{config.REQUEST_RETRIES}): {exc}"
                )
                time.sleep(2)
        logger.error(f"Giving up on page id={page_id} after {config.REQUEST_RETRIES} retries.")
        return None

    def _fetch_batch_with_ancestors(self, start: int, limit: int) -> list[dict]:
        """Same as _fetch_batch but adds 'ancestors' to the expand string."""
        for attempt in range(config.REQUEST_RETRIES):
            try:
                return self.confluence.get_all_pages_from_space(
                    space=config.SPACE_KEY,
                    start=start,
                    limit=limit,
                    expand="body.view,version,metadata.labels,ancestors",
                    content_type="page",
                ) or []
            except RequestException as exc:
                logger.warning(
                    f"Batch fetch (ancestors) failed at start={start} "
                    f"(attempt {attempt + 1}/{config.REQUEST_RETRIES}): {exc}"
                )
                time.sleep(2)
        raise RuntimeError(
            f"Failed to fetch pages at start={start} after {config.REQUEST_RETRIES} retries."
        )

    def _iter_by_cql(self, cql: str, expand: str) -> Iterator[dict]:
        """
        Stream all pages matching a CQL expression via
        /rest/api/content/search, using start/limit pagination.
        """
        url = f"{config.DOCUPEDIA_BASE_URL}/rest/api/content/search"
        start = 0
        batch_size = config.CRAWL_BATCH_SIZE
        while True:
            try:
                resp = self.confluence.session.get(
                    url,
                    params={
                        "cql": cql,
                        "start": start,
                        "limit": batch_size,
                        "expand": expand,
                    },
                    timeout=config.REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                raise RuntimeError(f"CQL search failed at start={start}: {exc}") from exc

            results = data.get("results", [])
            yield from results

            if len(results) < batch_size:
                break
            start += batch_size
            time.sleep(config.REQUEST_DELAY)

    def iter_pages_from_root(self, root_page_id: int) -> Iterator[dict]:
        """
        Yield the root page and all its descendants.

        Fast path (CQL): uses ancestor=<id> query against
        /rest/api/content/search — only fetches the subtree, not the whole
        space.

        Fallback: if CQL returns an error, scans all space pages with
        ancestors expanded and filters in Python.

        Respects config.MAX_PAGES (0 = unlimited).
        """
        total_yielded = 0

        def _to_page_dict(page: dict) -> dict:
            labels = (
                page.get("metadata", {})
                    .get("labels", {})
                    .get("results", [])
            )
            return {
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

        # ── Step 1: root page via get_page_by_id ─────────────────────────
        logger.info(f"Fetching root page {root_page_id}...")
        root = self._fetch_page(root_page_id)
        if root is None:
            logger.error(f"Root page {root_page_id} could not be fetched. Aborting.")
            return
        yield _to_page_dict(root)
        total_yielded += 1
        if config.MAX_PAGES > 0 and total_yielded >= config.MAX_PAGES:
            return

        # ── Step 2: descendants — CQL fast path, ancestor scan fallback ──
        cql = f'ancestor={root_page_id} AND space="{config.SPACE_KEY}"'
        expand = "body.view,version,metadata.labels"
        use_cql = False

        try:
            probe = self.confluence.session.get(
                f"{config.DOCUPEDIA_BASE_URL}/rest/api/content/search",
                params={"cql": cql, "start": 0, "limit": 1},
                timeout=config.REQUEST_TIMEOUT,
            )
            probe.raise_for_status()
            use_cql = True
            logger.info("CQL search supported — fetching descendants via CQL.")
        except Exception as exc:
            logger.warning(
                f"CQL not available ({exc}), falling back to full-space ancestor scan."
            )

        if use_cql:
            for page in self._iter_by_cql(cql, expand):
                if int(page["id"]) == root_page_id:
                    continue
                yield _to_page_dict(page)
                total_yielded += 1
                if config.MAX_PAGES > 0 and total_yielded >= config.MAX_PAGES:
                    logger.info(f"Reached MAX_PAGES={config.MAX_PAGES}. Stopping.")
                    return
        else:
            # Ancestor scan fallback: fetch all space pages with ancestors
            # expanded, filter in Python for descendants of root_page_id.
            logger.info(f"Scanning space for descendants of page {root_page_id}...")
            start = 0
            batch_size = config.CRAWL_BATCH_SIZE
            while True:
                pages = self._fetch_batch_with_ancestors(start, batch_size)
                if not pages:
                    break
                for page in pages:
                    if int(page["id"]) == root_page_id:
                        continue
                    ancestor_ids = {int(a["id"]) for a in page.get("ancestors", [])}
                    if root_page_id not in ancestor_ids:
                        continue
                    yield _to_page_dict(page)
                    total_yielded += 1
                    if config.MAX_PAGES > 0 and total_yielded >= config.MAX_PAGES:
                        logger.info(f"Reached MAX_PAGES={config.MAX_PAGES}. Stopping.")
                        return
                if len(pages) < batch_size:
                    break
                start += batch_size
                time.sleep(config.REQUEST_DELAY)

