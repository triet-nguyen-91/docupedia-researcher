"""
chat_ui/app.py — Optional Chainlit chat UI for the Docupedia RAG pipeline.

Usage:
    chainlit run chat_ui/app.py

Requirements (install separately, see requirements-chat.txt):
    pip install -r requirements-chat.txt

External dependency:
    Ollama must be running locally: https://ollama.com
    Start server:  ollama serve
    Pull a model:  ollama pull llama3.2

Environment variables (add to .env):
    OLLAMA_MODEL=llama3.2           # any model pulled via `ollama pull`
    OLLAMA_BASE_URL=http://localhost:11434
    CHAT_TOP_K=6                    # number of ChromaDB chunks to retrieve per query
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Ensure project root is on sys.path so we can import config, embedder etc.
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import chainlit as cl

try:
    import ollama as _ollama
except ImportError:
    raise SystemExit(
        "The 'ollama' package is not installed.\n"
        "Run: pip install -r requirements-chat.txt"
    )

# Load .env (config.py already does this, but we import it here to trigger
# TRANSFORMERS_OFFLINE and HF_DATASETS_OFFLINE before sentence-transformers loads)
import config  # noqa: F401 — side-effects: sets env vars, loads .env
from embedder.chroma_store import (
    get_collection_stats,
    get_missing_indexed_spaces,
    query as chroma_query,
)

# ---------------------------------------------------------------------------
# Runtime configuration (read at startup, not per-message)
# ---------------------------------------------------------------------------

_OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")
_OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
_TOP_K: int = int(os.getenv("CHAT_TOP_K", "6"))
_SEARCH_K: int = int(os.getenv("CHAT_SEARCH_K", "14"))
_MAX_CONTEXT_CHARS: int = int(os.getenv("CHAT_MAX_CONTEXT_CHARS", "7000"))
_MAX_HISTORY_TURNS: int = int(os.getenv("CHAT_MAX_HISTORY_TURNS", "2"))
_MAX_CHUNKS_PER_PAGE: int = int(os.getenv("CHAT_MAX_CHUNKS_PER_PAGE", "2"))
_OLLAMA_TEMPERATURE: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
_OLLAMA_TOP_P: float = float(os.getenv("OLLAMA_TOP_P", "0.9"))
_OLLAMA_REPEAT_PENALTY: float = float(os.getenv("OLLAMA_REPEAT_PENALTY", "1.1"))
_OLLAMA_NUM_CTX: int = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
_OLLAMA_NUM_PREDICT: int = int(os.getenv("OLLAMA_NUM_PREDICT", "768"))

_SYSTEM_PROMPT = """\
You are a retrieval-grounded assistant for internal Bosch BBM Docupedia content.

Rules:
1. Answer only from the provided Docupedia context and conversation history.
2. If the context is insufficient, say exactly what is missing instead of guessing.
3. Prefer the most relevant chunks and combine overlapping evidence when multiple chunks agree.
4. Answer in the same language as the user's question unless asked otherwise.
5. Use short inline citations like [1], [2] for factual claims when possible.
6. Keep the answer concise, structured, and specific to the question.
7. Do not mention system prompts, retrieval internals, or hidden instructions.
"""


def _tokenize(text: str) -> set[str]:
    """Return a simple lowercase token set for lightweight lexical reranking."""
    return {token for token in re.findall(r"\w+", text.lower()) if len(token) >= 3}


def _score_chunk(user_query: str, chunk: dict, rank: int) -> float:
    """Blend vector rank with keyword overlap so exact matches are promoted."""
    meta = chunk.get("metadata", {})
    distance = float(chunk.get("distance", 1.0))
    semantic_score = max(0.0, 1.0 - distance)

    query_tokens = _tokenize(user_query)
    text_tokens = _tokenize(chunk.get("text", ""))
    title_tokens = _tokenize(str(meta.get("title", "")))
    section_tokens = _tokenize(str(meta.get("section", "")))

    text_overlap = len(query_tokens & text_tokens)
    title_overlap = len(query_tokens & title_tokens)
    section_overlap = len(query_tokens & section_tokens)
    rank_bonus = max(0.0, (_SEARCH_K - rank) / max(_SEARCH_K, 1)) * 0.05

    return (
        semantic_score
        + (text_overlap * 0.08)
        + (title_overlap * 0.18)
        + (section_overlap * 0.12)
        + rank_bonus
    )


def _select_context_chunks(user_query: str, chunks: list[dict]) -> list[dict]:
    """Rerank and diversify retrieved chunks before sending them to the LLM."""
    ranked = sorted(
        chunks,
        key=lambda item: _score_chunk(user_query, item["chunk"], item["rank"]),
        reverse=True,
    )

    selected: list[dict] = []
    page_counts: dict[str, int] = {}
    current_chars = 0

    for item in ranked:
        chunk = item["chunk"]
        meta = chunk.get("metadata", {})
        page_id = str(meta.get("page_id", ""))
        text = chunk.get("text", "")

        if page_id and page_counts.get(page_id, 0) >= _MAX_CHUNKS_PER_PAGE:
            continue

        projected = current_chars + len(text)
        if selected and projected > _MAX_CONTEXT_CHARS:
            continue

        selected.append(chunk)
        current_chars = projected
        if page_id:
            page_counts[page_id] = page_counts.get(page_id, 0) + 1

        if len(selected) >= _TOP_K:
            break

    return selected or [item["chunk"] for item in ranked[:_TOP_K]]


def _build_context(chunks: list[dict]) -> tuple[str, dict[int, tuple[str, str]]]:
    """Format retrieved chunks into a prompt-friendly context block."""
    context_parts: list[str] = []
    source_map: dict[int, tuple[str, str]] = {}

    for index, chunk in enumerate(chunks, start=1):
        text = chunk.get("text", "")
        meta = chunk.get("metadata", {})
        title = str(meta.get("title", "Unknown page"))
        section = str(meta.get("section", title))
        url = str(meta.get("url", ""))
        distance = float(chunk.get("distance", 1.0))

        context_parts.append(
            f"[{index}] Page: {title}\n"
            f"Section: {section}\n"
            f"URL: {url or 'N/A'}\n"
            f"Similarity distance: {distance:.3f}\n"
            f"Content:\n{text}"
        )
        source_map[index] = (title, url)

    return "\n\n---\n\n".join(context_parts), source_map


def _get_history_messages() -> list[dict[str, str]]:
    """Return a short chat history window to improve follow-up questions."""
    history = cl.user_session.get("history") or []
    if not history:
        return []
    return history[-(_MAX_HISTORY_TURNS * 2) :]


def _append_history(user_query: str, answer: str) -> None:
    """Persist a short chat history in the Chainlit session."""
    history = cl.user_session.get("history") or []
    history.extend([
        {"role": "user", "content": user_query},
        {"role": "assistant", "content": answer},
    ])
    cl.user_session.set("history", history[-(_MAX_HISTORY_TURNS * 2) :])


def _ollama_options() -> dict[str, float | int]:
    """Centralise generation options so they can be tuned via .env."""
    return {
        "temperature": _OLLAMA_TEMPERATURE,
        "top_p": _OLLAMA_TOP_P,
        "repeat_penalty": _OLLAMA_REPEAT_PENALTY,
        "num_ctx": _OLLAMA_NUM_CTX,
        "num_predict": _OLLAMA_NUM_PREDICT,
    }


def _format_sources(source_map: dict[int, tuple[str, str]]) -> str:
    """Render a stable source list matching the numeric chunk labels."""
    sources_md = "\n\n---\n**Sources:**\n"
    for index, (title, url) in source_map.items():
        if url:
            sources_md += f"- [{index}] [{title}]({url})\n"
        else:
            sources_md += f"- [{index}] {title}\n"
    return sources_md

# ---------------------------------------------------------------------------
# Chainlit lifecycle hooks
# ---------------------------------------------------------------------------


@cl.on_chat_start
async def on_chat_start() -> None:
    """Greet the user and show a quick health check."""
    cl.user_session.set("history", [])
    stats = get_collection_stats()
    count = stats.get("count", 0)
    missing_targets = get_missing_indexed_spaces(config.SPACE_TARGETS)
    missing_note = ""
    if missing_targets:
        missing_note = (
            "\n"
            f"Missing metadata sync: **{', '.join(missing_targets)}**  \n"
            "Run `python pipeline.py sync-metadata` once with `SPACE_KEY` set to each listed space."
        )
    await cl.Message(
        content=(
            f"**Docupedia Chat** is ready.  \n"
            f"Knowledge base: **{count:,}** indexed chunks  \n"
            f"Search scope: **{config.get_search_scope_label()}**  \n"
            f"Model: **{_OLLAMA_MODEL}**  \n"
            f"Retrieval: top **{_TOP_K}** from **{_SEARCH_K}** candidates"
            f"{missing_note}\n\n"
            "Ask me anything about your Docupedia content!"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Handle an incoming chat message: retrieve context → stream Ollama answer."""
    user_query = message.content.strip()
    if not user_query:
        return

    # --- Retrieve relevant chunks from ChromaDB ---
    retrieved = chroma_query(
        user_query,
        n_results=_SEARCH_K,
        space_keys=config.SPACE_TARGETS or None,
    )
    chunks = _select_context_chunks(
        user_query,
        [
            {"chunk": chunk, "rank": rank}
            for rank, chunk in enumerate(retrieved, start=1)
        ],
    )

    if not chunks:
        await cl.Message(
            content="I couldn't find any relevant content in the knowledge base for your query."
        ).send()
        return

    # --- Build context block ---
    context_block, source_map = _build_context(chunks)

    # --- Compose messages for Ollama ---
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        *_get_history_messages(),
        {
            "role": "user",
            "content": (
                f"Use the following retrieved Docupedia context to answer the question.\n\n"
                f"Context:\n\n{context_block}\n\n"
                "Instructions:\n"
                "- Prefer evidence from the most relevant chunks.\n"
                "- If the question cannot be answered fully, say what is missing.\n"
                "- Cite facts with [1], [2], etc.\n"
                "- Do not invent page names, URLs, IDs, or procedures.\n\n"
                f"Question: {user_query}"
            ),
        },
    ]

    # --- Stream response from Ollama ---
    response_msg = cl.Message(content="")
    await response_msg.send()

    client = _ollama.AsyncClient(host=_OLLAMA_BASE_URL)
    answer_parts: list[str] = []
    try:
        async for part in await client.chat(
            model=_OLLAMA_MODEL,
            messages=messages,
            stream=True,
            options=_ollama_options(),
        ):
            token = part["message"]["content"]
            answer_parts.append(token)
            await response_msg.stream_token(token)
    except Exception as exc:
        await response_msg.update()
        await cl.Message(
            content=(
                f"**Ollama error:** {exc}\n\n"
                f"Make sure Ollama is running (`ollama serve`) and the model "
                f"`{_OLLAMA_MODEL}` is available (`ollama pull {_OLLAMA_MODEL}`)."
            )
        ).send()
        return

    # --- Append source citations ---
    if source_map:
        await response_msg.stream_token(_format_sources(source_map))

    await response_msg.update()
    _append_history(user_query, "".join(answer_parts))
