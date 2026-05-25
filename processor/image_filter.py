"""
processor/image_filter.py

Decide whether a given page attachment is worth OCR-ing.

The filter pipeline is intentionally layered from cheapest to most expensive:

    1. Hard rules    — extension / filename blocklist (no file I/O)
    2. Cheap rules   — width / height / aspect ratio / byte size (Pillow header only)
    3. Content rules — duplicate hash within a page (sha1 of file bytes)

Each image is classified into:

    {
        "is_indexable": bool,
        "reason":       str | None,   # populated when is_indexable=False
        "width":        int | None,
        "height":       int | None,
        "byte_size":    int | None,
        "mime_type":    str | None,
        "hash":         str | None,   # sha1, only computed when needed
    }

This module never imports tesseract — it is safe to run on machines without it.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

from PIL import Image, UnidentifiedImageError

sys.path.insert(0, str(Path(__file__).parent.parent))

import config

logger = logging.getLogger(__name__)

# Extensions we accept for OCR. SVG is excluded — vector text should be parsed
# directly from XML, not run through tesseract.
_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}

# Lightweight extension → MIME map. Pillow's resolved format wins if available.
_EXT_MIME = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".bmp":  "image/bmp",
    ".tif":  "image/tiff",
    ".tiff": "image/tiff",
}


def evaluate_image(local_path: Path, seen_hashes: set[str] | None = None) -> dict:
    """Classify a single image file. See module docstring for return shape."""
    result: dict = {
        "is_indexable": False,
        "reason": None,
        "width": None,
        "height": None,
        "byte_size": None,
        "mime_type": None,
        "hash": None,
    }

    # ── 1. Hard rules: extension / filename blocklist ─────────────────────
    suffix = local_path.suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        result["reason"] = f"unsupported_extension:{suffix or 'none'}"
        return result
    result["mime_type"] = _EXT_MIME.get(suffix)

    filename_lower = local_path.name.lower()
    for pattern in config.OCR_BLOCKLIST_PATTERNS:
        if pattern and pattern in filename_lower:
            result["reason"] = f"blocklisted_name:{pattern}"
            return result

    # ── 2. File system level checks ───────────────────────────────────────
    try:
        stat = local_path.stat()
    except OSError as exc:
        result["reason"] = f"stat_failed:{exc}"
        return result

    result["byte_size"] = stat.st_size
    if stat.st_size < config.OCR_MIN_BYTES:
        result["reason"] = f"too_small_bytes:{stat.st_size}"
        return result

    # ── 3. Pillow header-only check (dimensions, format) ──────────────────
    try:
        with Image.open(local_path) as img:
            img_format = (img.format or "").lower()
            width, height = img.size
    except (UnidentifiedImageError, OSError) as exc:
        result["reason"] = f"unreadable_image:{exc}"
        return result

    result["width"], result["height"] = width, height
    if img_format:
        # Pillow uses uppercase short codes ("PNG", "JPEG"). Map to MIME when we can.
        fmt_key = "." + img_format
        if fmt_key in _EXT_MIME:
            result["mime_type"] = _EXT_MIME[fmt_key]

    if width < config.OCR_MIN_WIDTH or height < config.OCR_MIN_HEIGHT:
        result["reason"] = f"too_small_dims:{width}x{height}"
        return result

    if width * height > config.OCR_MAX_PIXELS:
        # Not a fatal reject — the OCR step is expected to downscale before
        # processing. We just flag it for visibility.
        logger.debug(
            "Image %s exceeds OCR_MAX_PIXELS (%d > %d) — will be downscaled.",
            local_path.name, width * height, config.OCR_MAX_PIXELS,
        )

    ratio = max(width, height) / max(1, min(width, height))
    if ratio > config.OCR_MAX_RATIO:
        result["reason"] = f"extreme_aspect_ratio:{ratio:.1f}"
        return result

    # ── 4. Duplicate-by-hash (per-page scope) ─────────────────────────────
    img_hash = _sha1(local_path)
    result["hash"] = img_hash
    if seen_hashes is not None:
        if img_hash in seen_hashes:
            result["reason"] = "duplicate_hash"
            return result
        seen_hashes.add(img_hash)

    result["is_indexable"] = True
    return result


def _sha1(path: Path) -> str:
    """Stream sha1 of a file. Robust to large attachments."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
