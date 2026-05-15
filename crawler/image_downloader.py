"""
crawler/image_downloader.py

Download all Confluence page attachments to local storage and rewrite the raw
HTML so that image/media/link URLs and CSS background-image references point
to the local copies instead of the live Confluence server.

This mirrors the proven pattern from the working POC (docupedia_crawler.py):
  1. Fetch all attachments via Confluence API
  2. Download each file into data/images/<pageid>/
  3. Rewrite <img>, <video>, <a> src/href attributes in the HTML
  4. Rewrite CSS background-image URLs in inline style attributes
  5. Return the rewritten "offline" HTML and a list of download results

Public API:
  download_page_images(client, page_id, raw_html)
      → (offline_html: str, results: list[dict])
"""

from __future__ import annotations

import logging
import random
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import config

logger = logging.getLogger(__name__)


def download_page_images(
    client,          # ConfluenceClient — not imported directly to avoid circular
    page_id: int,
    raw_html: str,
) -> tuple[str, list[dict]]:
    """
    Download all attachments for *page_id* and rewrite URLs in *raw_html*.

    Args:
        client:   ConfluenceClient instance (exposes .confluence).
        page_id:  Confluence page ID.
        raw_html: Raw HTML string from body.view.value.

    Returns:
        (offline_html, results) where:
          offline_html — HTML with attachment URLs rewritten to local paths
          results      — list of dicts (one per attachment):
            {
                "filename":   str,
                "local_path": str | None,
                "url":        str | None,
                "skipped":    bool,
                "reason":     str,
            }
    """
    # Per-page image directory: data/images/<pageid>/
    page_img_dir = config.IMAGES_DIR / str(page_id)
    page_img_dir.mkdir(parents=True, exist_ok=True)

    try:
        attachments_data = client.confluence.get_attachments_from_content(
            str(page_id), limit=1000
        )
    except Exception as exc:
        logger.warning(f"Could not fetch attachments for page {page_id}: {exc}")
        return raw_html, []

    if not attachments_data or "results" not in attachments_data:
        return raw_html, []

    session = client.confluence.session
    session.trust_env = False
    base_url = client.confluence.url.rstrip("/")

    url_map: dict[str, str] = {}   # download_link → relative local path
    results: list[dict] = []

    for att in attachments_data["results"]:
        filename = _sanitize_filename(att.get("title", "unknown"))
        download_link: str = att.get("_links", {}).get("download", "")
        if not download_link:
            results.append(_skipped(filename, "no_download_link"))
            continue

        local_path = page_img_dir / filename
        url = base_url + download_link

        if local_path.exists():
            logger.debug(f"Already exists, skipping: {filename}")
            url_map[download_link] = str(local_path)
            results.append({
                "filename": filename,
                "local_path": str(local_path),
                "url": url,
                "skipped": True,
                "reason": "already_exists",
            })
        else:
            try:
                resp = session.get(url, stream=True, verify=False,
                                   timeout=config.REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    with open(local_path, "wb") as f:
                        for chunk in resp.iter_content(1024):
                            f.write(chunk)
                    logger.debug(f"Downloaded attachment: {filename}")
                    url_map[download_link] = str(local_path)
                    results.append({
                        "filename": filename,
                        "local_path": str(local_path),
                        "url": url,
                        "skipped": False,
                        "reason": "",
                    })
                else:
                    logger.warning(f"Failed to download {filename}: HTTP {resp.status_code}")
                    results.append(_skipped(filename, f"http_{resp.status_code}", url))
            except Exception as exc:
                logger.warning(f"Error downloading {filename}: {exc}")
                results.append(_skipped(filename, str(exc), url))

        # Random delay between attachment downloads (mirrors POC)
        time.sleep(random.uniform(1.0, 3.0))

    # Rewrite HTML tags and CSS urls
    offline_html = _rewrite_html(raw_html, url_map, base_url, session, page_img_dir)
    return offline_html, results


# ---------------------------------------------------------------------------
# HTML rewriting helpers
# ---------------------------------------------------------------------------

def _rewrite_html(
    html: str,
    url_map: dict[str, str],
    base_url: str,
    session,
    page_img_dir: Path,
) -> str:
    """
    Rewrite attachment URLs in HTML to local file paths.
    Handles <img src>, <video src>, <a href>, and inline style background-image.
    """
    soup = BeautifulSoup(html, "html.parser")

    # ── <img>, <video>, <a> ──────────────────────────────────────────────
    for tag in soup.find_all(["img", "video", "a"]):
        attr = "src" if tag.name in ["img", "video"] else "href"
        if not tag.has_attr(attr):
            continue
        old_val: str = tag[attr]
        for link, local in url_map.items():
            if link in old_val:
                tag[attr] = local
                break

    # ── inline CSS background-image ──────────────────────────────────────
    for tag in soup.find_all(style=True):
        style_val: str = tag["style"]
        css_urls = re.findall(r"url\(['\"]?(.*?)['\"]?\)", style_val)
        for css_url in css_urls:
            if "/download/attachments/" not in css_url:
                continue
            filename = _sanitize_filename(Path(css_url.split("?")[0]).name)
            local_path = page_img_dir / filename
            full_url = base_url + css_url if css_url.startswith("/") else css_url
            if local_path.exists():
                style_val = style_val.replace(css_url, str(local_path))
            else:
                try:
                    resp = session.get(full_url, stream=True, verify=False,
                                       timeout=config.REQUEST_TIMEOUT)
                    if resp.status_code == 200:
                        with open(local_path, "wb") as f:
                            for chunk in resp.iter_content(1024):
                                f.write(chunk)
                        style_val = style_val.replace(css_url, str(local_path))
                        logger.debug(f"Downloaded CSS resource: {filename}")
                    else:
                        logger.warning(f"CSS resource {filename}: HTTP {resp.status_code}")
                except Exception as exc:
                    logger.warning(f"Error downloading CSS resource {filename}: {exc}")
        tag["style"] = style_val

    return str(soup)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _sanitize_filename(name: str) -> str:
    """Replace characters invalid in Windows/Linux filenames with underscore."""
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name)


def _skipped(filename: str, reason: str, url: str | None = None) -> dict:
    return {
        "filename": filename,
        "local_path": None,
        "url": url,
        "skipped": True,
        "reason": reason,
    }
