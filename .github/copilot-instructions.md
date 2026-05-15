# BBM Docupedia RAG Pipeline — Workspace Instructions

This project is a Python RAG (Retrieval-Augmented Generation) pipeline that crawls an internal Bosch Confluence space (Docupedia), stores content in ChromaDB, and exposes it to GitHub Copilot via a local MCP server.

## Key conventions

- All configuration (paths, credentials, model settings) is centralised in `config.py`. Every module imports from `config` — never hardcode paths or credentials anywhere else.
- `.env` holds secrets (`DOCUPEDIA_PAT`, `DOCUPEDIA_BASE_URL`, `SPACE_KEY`). Never commit `.env`.
- Authentication uses a PAT Bearer token set automatically on every Confluence API request at construction time. No explicit login call is needed anywhere.
- The virtual environment is `.venv/`. Always activate it before running commands.
- PX Proxy (`http://127.0.0.1:3128`) is required for `pip install` and Confluence API requests.
- JSON files in `data/raw/` are named `<pageid>_<title>.json`. The title is sanitised with `re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)[:100]`.

## Module responsibilities

| Module | Purpose |
|---|---|
| `crawler/confluence_client.py` | Fetches all pages from `SPACE_KEY` with HTML body inline in batch API calls |
| `crawler/page_parser.py` | Converts Confluence HTML → structured `{"sections": [...]}` dict using BeautifulSoup + markdownify |
| `crawler/image_downloader.py` | Downloads all page attachments, rewrites HTML `src`/`href` URLs to local `data/images/<pageid>/` paths |
| `processor/chunker.py` | Splits sections into `CHUNK_SIZE`-token chunks; IDs are `<pageid>-<index>` |
| `processor/markdown_writer.py` | Writes `<pageid>_<title>.md` to `data/docupedia_data_page/` with YAML frontmatter |
| `embedder/embedding_model.py` | Lazy-loads `intfloat/multilingual-e5-base`; applies `passage:`/`query:` prefixes |
| `embedder/chroma_store.py` | `upsert_chunks()` (idempotent) and `query()` against local ChromaDB persistent store |
| `mcp_server/server.py` | FastMCP stdio server — exposes `search_docs`, `get_page`, `list_pages` to Copilot |
| `pipeline.py` | CLI entry point: `crawl`, `embed`, `run` subcommands |

## Data layout

All crawl output is scoped under `<SPACE_KEY>` so multiple spaces coexist without conflicts.

```
data/raw/<SPACE_KEY>/<pageid>_<title>.json                ← structured sections + metadata
data/raw_html/<SPACE_KEY>/<pageid>.html                   ← offline HTML (attachment URLs rewritten to local)
data/images/<SPACE_KEY>/<pageid>/                         ← downloaded page attachments
data/docupedia_data_page/<SPACE_KEY>/<pageid>_<title>.md  ← Markdown export per page
data/chroma_db/                                           ← ChromaDB vector store (shared across all spaces)
```

## Running the pipeline

```powershell
# Activate venv first
.venv\Scripts\Activate.ps1

python pipeline.py crawl          # fetch pages → save HTML + JSON + Markdown
python pipeline.py embed          # chunk + embed → ChromaDB
python pipeline.py run            # crawl + embed in one shot
python pipeline.py crawl --limit 5  # test with 5 pages only

# MCP server (VS Code starts this automatically via .vscode/mcp.json)
python -m mcp_server.server
```

## Embedding model

- Model: `intfloat/multilingual-e5-base` (768-dim, ~280MB, cross-lingual)
- Documents are prefixed with `"passage: "` before embedding
- Queries are prefixed with `"query: "` before embedding
- Supports cross-lingual retrieval: Vietnamese/English query → English/German content
