"""
processor/ocr.py

Build the ``images_index`` for a parsed page and run OCR on the indexable
attachments. Designed to be called by ``pipeline.py ocr`` between the crawl
and embed stages.

Public API:
    build_images_index(page_id, raw_html) → list[dict]
        Scan offline HTML for <img> tags, infer section context, but do NOT
        run OCR. Pure HTML+filesystem inspection.

    run_ocr_for_page(parsed_page, force=False) → tuple[list[dict], dict]
        Build/refresh ``images_index`` and run OCR for entries that need it.
        Returns (images_index, summary_stats).

The OCR phase is fault-tolerant: tesseract or its language packs missing is
treated as a warning, never a hard failure of the pipeline.
"""

from __future__ import annotations

import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean
from typing import Iterable

from bs4 import BeautifulSoup, Tag
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from processor.image_filter import evaluate_image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tesseract availability is detected lazily so importing this module never
# blows up on machines that do not have tesseract installed yet.
# ---------------------------------------------------------------------------
_TESSERACT_OK: bool | None = None
_TESSERACT_REASON: str = ""


def _ensure_tesseract() -> bool:
    """Lazy probe for pytesseract + tesseract binary. Result is cached."""
    global _TESSERACT_OK, _TESSERACT_REASON
    if _TESSERACT_OK is not None:
        return _TESSERACT_OK

    try:
        import pytesseract  # noqa: F401
    except ImportError as exc:
        _TESSERACT_OK = False
        _TESSERACT_REASON = f"pytesseract_not_installed:{exc}"
        return False

    import pytesseract  # local import keeps the global namespace clean
    if config.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD

    try:
        version = pytesseract.get_tesseract_version()
        logger.info("Tesseract %s detected (langs=%s).", version, config.OCR_LANGS)
        _TESSERACT_OK = True
    except Exception as exc:  # binary missing or unreadable
        _TESSERACT_OK = False
        _TESSERACT_REASON = f"tesseract_binary_missing:{exc}"
        logger.warning(
            "Tesseract is not callable (%s). OCR will be skipped — install the binary "
            "and (optionally) set TESSERACT_CMD in .env.", exc,
        )
    return _TESSERACT_OK


# ---------------------------------------------------------------------------
# HTML walk — derive (filename, section_heading) for every <img>
# ---------------------------------------------------------------------------

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _iter_image_records_from_html(html: str) -> Iterable[dict]:
    """
    Walk the offline HTML and yield one record per <img> with section context.

    Each yielded dict carries: ``src`` (local path string), ``alt``, ``caption_dom``,
    ``section``, ``section_index`` and ``order``. ``src`` is whatever the rewritten
    HTML carries — typically an absolute path under ``data/images/<SPACE_KEY>/...``.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Mirror page_parser's unwrap so headings live at the iteration level.
    real_children = [el for el in soup.children if getattr(el, "name", None)]
    root = real_children[0] if len(real_children) == 1 and real_children[0].name == "div" else soup

    current_heading: str | None = None
    section_index = 0
    image_order = 0

    for element in root.descendants:
        name = getattr(element, "name", None)
        if name in _HEADING_TAGS:
            current_heading = element.get_text(strip=True) or None
            section_index += 1
            continue
        if name != "img":
            continue

        tag: Tag = element  # type: ignore[assignment]
        src = (tag.get("src") or "").strip()
        if not src:
            continue
        alt = (tag.get("alt") or "").strip() or None
        caption_dom = _nearby_caption(tag)

        yield {
            "src": src,
            "alt": alt,
            "caption_dom": caption_dom,
            "section": current_heading,
            "section_index": section_index,
            "order": image_order,
        }
        image_order += 1


def _nearby_caption(tag: Tag) -> str | None:
    """Return text from a sibling/parent <figcaption> or Confluence caption div."""
    parent = tag.parent
    while parent is not None and getattr(parent, "name", None) not in {"figure", "div", None}:
        parent = parent.parent
    if parent is None:
        return None
    cap = parent.find("figcaption")
    if cap is None:
        cap = parent.find(class_=re.compile(r"caption", re.I))
    if cap is None:
        return None
    text = cap.get_text(" ", strip=True)
    return text or None


# ---------------------------------------------------------------------------
# Building the per-page images index
# ---------------------------------------------------------------------------

def build_images_index(page_id: int, raw_html: str) -> list[dict]:
    """
    Build the ``images_index`` list for one page from the offline HTML.

    Does **not** open the image files yet — that happens in ``run_ocr_for_page``.
    Records are deduplicated by ``local_path`` to avoid double-counting the same
    attachment referenced multiple times on a single page.
    """
    images: dict[str, dict] = {}
    page_dir = config.IMAGES_DIR / str(page_id)

    for record in _iter_image_records_from_html(raw_html):
        local_path = _resolve_local_path(record["src"], page_dir)
        if local_path is None:
            continue

        key = str(local_path)
        if key in images:
            continue  # first occurrence wins (preserves section context)

        images[key] = {
            "image_id": f"{page_id}-img-{len(images)}",
            "page_id": page_id,
            "space_key": config.SPACE_KEY,
            "filename": local_path.name,
            "local_path": str(local_path),
            "section": record["section"],
            "section_index": record["section_index"],
            "order": record["order"],
            "alt_text": record["alt"],
            "caption_dom": record["caption_dom"],
        }

    return list(images.values())


def _resolve_local_path(src: str, page_dir: Path) -> Path | None:
    """Resolve an <img src> into a Path on disk, if it points to a real file."""
    if not src:
        return None
    # image_downloader rewrites src to an absolute local path (str of Path).
    candidate = Path(src)
    if candidate.is_file():
        return candidate
    # Fallback: filename only, expected under data/images/<SPACE_KEY>/<pageid>/.
    by_name = page_dir / Path(src).name
    if by_name.is_file():
        return by_name
    return None


# ---------------------------------------------------------------------------
# OCR runner
# ---------------------------------------------------------------------------

def run_ocr_for_page(parsed_page: dict, force: bool = False) -> tuple[list[dict], dict]:
    """
    Build/refresh ``images_index`` for *parsed_page* and run OCR on entries
    that still need it.

    Returns a tuple of (images_index, summary) where ``summary`` is a dict of
    counters useful for the CLI report.
    """
    summary = {
        "total": 0,
        "skipped_filter": 0,
        "ocr_good": 0,
        "ocr_partial": 0,
        "ocr_empty": 0,
        "ocr_error": 0,
        "skipped_existing": 0,
    }

    page_id = int(parsed_page["pageid"])

    # ── Discover the offline HTML so we can attach section context ───────
    html_path = config.RAW_HTML_DIR / f"{page_id}.html"
    if not html_path.is_file():
        logger.warning("Offline HTML missing for page %s — cannot build images_index.", page_id)
        return parsed_page.get("images_index", []), summary

    raw_html = html_path.read_text(encoding="utf-8", errors="ignore")
    fresh_index = build_images_index(page_id, raw_html)

    # Merge with any existing images_index so previous OCR results survive.
    prior_index = {item.get("local_path"): item for item in parsed_page.get("images_index", [])}
    for record in fresh_index:
        prior = prior_index.get(record["local_path"])
        if prior:
            # Preserve OCR fields unless force=True.
            for key in (
                "ocr_text", "ocr_words", "ocr_confidence", "ocr_lang",
                "needs_caption", "is_indexable", "filter_reason",
                "kind", "width", "height", "byte_size", "mime_type", "hash",
            ):
                if key in prior and key not in record:
                    record[key] = prior[key]

    # ── Filter pass ───────────────────────────────────────────────────────
    seen_hashes: set[str] = set()
    indexable: list[dict] = []
    for record in fresh_index:
        summary["total"] += 1
        if summary["total"] > config.OCR_PER_PAGE_LIMIT:
            record["is_indexable"] = False
            record["filter_reason"] = "per_page_limit"
            summary["skipped_filter"] += 1
            continue

        local_path = Path(record["local_path"])
        verdict = evaluate_image(local_path, seen_hashes=seen_hashes)
        record.update({
            "width": verdict["width"],
            "height": verdict["height"],
            "byte_size": verdict["byte_size"],
            "mime_type": verdict["mime_type"],
            "hash": verdict["hash"],
            "is_indexable": verdict["is_indexable"],
            "filter_reason": verdict["reason"],
        })
        if verdict["is_indexable"]:
            indexable.append(record)
        else:
            summary["skipped_filter"] += 1

    if not indexable or not config.OCR_ENABLED:
        return fresh_index, summary

    if not _ensure_tesseract():
        logger.warning(
            "Skipping OCR for page %s — tesseract unavailable (%s).",
            page_id, _TESSERACT_REASON,
        )
        return fresh_index, summary

    # ── OCR pass (concurrent) ────────────────────────────────────────────
    targets = [
        record for record in indexable
        if force or not record.get("ocr_text") and record.get("needs_ocr_done") is not True
    ]
    summary["skipped_existing"] = len(indexable) - len(targets)

    if not targets:
        return fresh_index, summary

    workers = max(1, config.OCR_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for record, ocr_result in zip(targets, pool.map(_ocr_one, targets)):
            _apply_ocr_result(record, ocr_result, summary)

    return fresh_index, summary


def _ocr_one(record: dict) -> dict:
    """Run tesseract on one image. Returns OCR fields or an error marker."""
    import pytesseract  # local — already proven available

    local_path = Path(record["local_path"])
    try:
        with Image.open(local_path) as img:
            prepared = _preprocess(img)
            data = pytesseract.image_to_data(
                prepared,
                lang=config.OCR_LANGS,
                output_type=pytesseract.Output.DICT,
            )
    except Exception as exc:
        logger.warning("OCR failed for %s: %s", local_path.name, exc)
        return {"error": str(exc)}

    words: list[str] = []
    confs: list[float] = []
    for text, conf in zip(data.get("text", []), data.get("conf", [])):
        token = (text or "").strip()
        if not token:
            continue
        try:
            conf_val = float(conf)
        except (TypeError, ValueError):
            continue
        if conf_val < 0:
            continue
        words.append(token)
        confs.append(conf_val)

    cleaned_text = " ".join(words)
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
    return {
        "ocr_text": cleaned_text,
        "ocr_words": len(words),
        "ocr_confidence": round(mean(confs), 1) if confs else 0.0,
        "ocr_lang": config.OCR_LANGS,
    }


def _preprocess(img: Image.Image) -> Image.Image:
    """Normalize an image for OCR: respect EXIF, downscale, convert to grayscale."""
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")

    # Downscale large images so tesseract does not choke on whiteboard photos.
    max_pixels = config.OCR_MAX_PIXELS
    if max_pixels > 0 and (img.width * img.height) > max_pixels:
        ratio = (max_pixels / float(img.width * img.height)) ** 0.5
        new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)

    return img.convert("L")


def _apply_ocr_result(record: dict, result: dict, summary: dict) -> None:
    """Write OCR fields back onto the record and update the running summary."""
    if "error" in result:
        record["ocr_text"] = ""
        record["ocr_words"] = 0
        record["ocr_confidence"] = 0.0
        record["ocr_lang"] = config.OCR_LANGS
        record["ocr_error"] = result["error"]
        record["needs_caption"] = True
        record["kind"] = record.get("kind") or "unknown"
        summary["ocr_error"] += 1
        return

    record["ocr_text"] = result["ocr_text"]
    record["ocr_words"] = result["ocr_words"]
    record["ocr_confidence"] = result["ocr_confidence"]
    record["ocr_lang"] = result["ocr_lang"]
    record.pop("ocr_error", None)

    words = result["ocr_words"]
    conf = result["ocr_confidence"]

    if words >= config.OCR_GOOD_WORDS and conf >= config.OCR_GOOD_CONF:
        summary["ocr_good"] += 1
        record["needs_caption"] = False
        record["kind"] = record.get("kind") or _guess_kind(record, words, "good")
    elif words >= config.OCR_PARTIAL_WORDS and conf >= config.OCR_PARTIAL_CONF:
        summary["ocr_partial"] += 1
        record["needs_caption"] = True
        record["kind"] = record.get("kind") or _guess_kind(record, words, "partial")
    else:
        summary["ocr_empty"] += 1
        record["needs_caption"] = True
        record["kind"] = record.get("kind") or _guess_kind(record, words, "empty")


def _guess_kind(record: dict, words: int, bucket: str) -> str:
    """Cheap heuristic for ``kind`` based on dimensions and OCR yield."""
    width = record.get("width") or 0
    height = record.get("height") or 0
    if not width or not height:
        return "unknown"
    ratio = width / max(1, height)

    if bucket == "good":
        if 1.2 <= ratio <= 2.2:
            return "screenshot"
        if words > 60:
            return "table"
        return "screenshot"
    if bucket == "partial":
        return "diagram"
    return "diagram" if 0.5 <= ratio <= 2.0 else "photo"
