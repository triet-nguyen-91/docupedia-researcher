"""
processor/chunker.py

Split a parsed page (from page_parser.py) into text chunks suitable for
embedding.  Uses LangChain's RecursiveCharacterTextSplitter so that chunks
respect natural text boundaries (paragraphs → sentences → words).

Each resulting chunk is a dict:
{
    "id":       "<pageid>-<chunk_index>",   # unique ID for ChromaDB
    "text":     str,                         # chunk body (with section context)
    "metadata": {
        "space_key":    str,
        "page_id":      int,
        "title":        str,
        "url":          str,
        "section":      str,
        "chunk_index":  int,
        "type":         "text",
        "last_modified": str,
    }
}
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# One shared splitter instance (config values are read at import time once).
_splitter = RecursiveCharacterTextSplitter(
    chunk_size=config.CHUNK_SIZE,
    chunk_overlap=config.CHUNK_OVERLAP,
    length_function=len,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def chunk_page(parsed_page: dict) -> list[dict]:
    """
    Split all sections of a parsed page into a flat list of chunk dicts.

    Args:
        parsed_page: dict returned by ``crawler.page_parser.parse_page()``.

    Returns:
        List of chunk dicts ready for ``embedder.chroma_store.upsert_chunks()``.
    """
    pageid = parsed_page["pageid"]
    title = parsed_page["title"]
    url = parsed_page.get("url", "")
    last_modified = parsed_page.get("last_modified", "")

    chunks: list[dict] = []
    chunk_index = 0

    for section in parsed_page.get("sections", []):
        heading = section.get("heading") or title
        text = section.get("text", "").strip()
        if not text:
            continue

        # Prepend the section heading so the embedding captures the topic
        section_text = f"{heading}\n\n{text}" if section.get("heading") else text

        splits = _splitter.split_text(section_text)
        for split in splits:
            if not split.strip():
                continue
            chunks.append({
                "id": f"{pageid}-{chunk_index}",
                "text": split,
                "metadata": {
                    "space_key": config.SPACE_KEY,
                    "page_id": pageid,
                    "title": title,
                    "url": url,
                    "section": heading,
                    "chunk_index": chunk_index,
                    "type": "text",
                    "last_modified": last_modified,
                },
            })
            chunk_index += 1

    # Image OCR chunks share the collection but live in their own ID namespace
    # so we never collide with text chunk IDs.
    chunks.extend(_image_ocr_chunks(parsed_page))

    logger.debug(f"Chunked page [{pageid}] '{title}': {len(chunks)} chunks total")
    return chunks


def _image_ocr_chunks(parsed_page: dict) -> list[dict]:
    """Build ``type=image_ocr`` chunks from the page's ``images_index``."""
    images_index = parsed_page.get("images_index") or []
    if not images_index:
        return []

    pageid = parsed_page["pageid"]
    title = parsed_page["title"]
    url = parsed_page.get("url", "")
    last_modified = parsed_page.get("last_modified", "")

    chunks: list[dict] = []

    for image in images_index:
        ocr_text = (image.get("ocr_text") or "").strip()
        if not image.get("is_indexable") or not ocr_text:
            continue

        heading = image.get("section") or title
        filename = image.get("filename") or ""
        alt = (image.get("alt_text") or "").strip()
        caption = (image.get("caption_dom") or "").strip()

        prelude_parts: list[str] = [f"{heading}"]
        if filename:
            prelude_parts.append(f"Image: {filename}")
        if alt:
            prelude_parts.append(f"Alt: {alt}")
        if caption:
            prelude_parts.append(f"Caption: {caption}")
        prelude = "\n".join(prelude_parts)

        full_text = f"{prelude}\n\nOCR:\n{ocr_text}"
        splits = _splitter.split_text(full_text)

        for k, split in enumerate(splits):
            text_value = split.strip()
            if not text_value:
                continue
            chunks.append({
                "id": f"{pageid}-img-{image.get('order', 0)}-ocr-{k}",
                "text": text_value,
                "metadata": {
                    "space_key": image.get("space_key") or config.SPACE_KEY,
                    "page_id": pageid,
                    "title": title,
                    "url": url,
                    "section": heading,
                    "type": "image_ocr",
                    "image_id": image.get("image_id", ""),
                    "filename": filename,
                    "local_path": image.get("local_path", ""),
                    "kind": image.get("kind", "unknown"),
                    "ocr_confidence": float(image.get("ocr_confidence") or 0.0),
                    "ocr_lang": image.get("ocr_lang") or config.OCR_LANGS,
                    "chunk_index": k,
                    "last_modified": last_modified,
                },
            })

    return chunks
