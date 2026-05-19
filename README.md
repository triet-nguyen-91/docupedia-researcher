# BBM Docupedia RAG Pipeline

A Retrieval-Augmented Generation (RAG) pipeline that crawls a **Confluence-based Docupedia** space using a Personal Access Token (PAT), downloads page HTML and attachments, parses content into structured sections, stores embeddings in ChromaDB, and exposes a **GitHub Copilot chat agent** for querying the knowledge base.

---

## Project Structure

```
BBM_Docupedia/
├── config.py                      # Central configuration — loads .env, defines all constants and paths
├── pipeline.py                    # Main entry point; orchestrates crawl and embed steps
├── requirements.txt               # Python dependencies
├── .env                           # Your local secrets (not committed to git)
├── .env.example                   # Template for .env
│
├── crawler/                       # Confluence crawling module
│   ├── confluence_client.py       # Confluence API client — PAT Bearer auth, paginated HTML fetch, retries
│   ├── page_parser.py             # Parses Confluence HTML into structured sections (BeautifulSoup + markdownify)
│   └── image_downloader.py        # Downloads page attachments; rewrites HTML URLs to local file paths
│
├── processor/                     # Content processing module
│   ├── chunker.py                 # Splits parsed pages into embedding-ready text chunks
│   └── markdown_writer.py         # Writes each page as a Markdown file
│
├── embedder/                      # Embedding and vector store module
│   ├── embedding_model.py         # Loads sentence-transformers model, runs inference
│   └── chroma_store.py            # Upserts/queries ChromaDB vector store
│
├── mcp_server/
│   └── server.py                  # FastMCP stdio server — exposes search_docs, get_page, list_pages
│
├── .vscode/
│   └── mcp.json                   # Auto-starts MCP server when workspace opens in VS Code
│
├── .github/
│   ├── copilot-instructions.md    # Workspace-wide Copilot context
│   └── agents/
│       └── docupedia.agent.md     # @Docupedia custom chat agent definition
│
└── docs/
    └── docupedia_rag_decisions.md # Architecture decision log
```

### Data directories (auto-created at runtime, not committed to git)

All crawl output is organised under a `<SPACE_KEY>` subdirectory so multiple spaces can coexist without conflicts. ChromaDB is shared across all spaces (one searchable vector store).

| Path | Contents |
|---|---|
| `data/raw/<SPACE_KEY>/<pageid>_<title>.json` | Structured JSON per page — sections, metadata, labels |
| `data/raw_html/<SPACE_KEY>/<pageid>.html` | Offline HTML per page — attachment URLs rewritten to local paths |
| `data/images/<SPACE_KEY>/<pageid>/` | Downloaded attachments per page |
| `data/docupedia_data_page/<SPACE_KEY>/<pageid>_<title>.md` | Markdown export per page |
| `data/chroma_db/` | Persistent ChromaDB vector store (shared across all spaces) |

---

## How It Works

```
Confluence API (PAT auth)
        │
        │  batch fetch — HTML body inline per request
        ▼
 confluence_client.py
        │
        │  raw HTML + metadata per page
        ▼
 image_downloader.py   ──── download attachments ──── data/images/<SPACE_KEY>/<pageid>/
        │
        │  offline HTML (local URLs)
        ▼
  data/raw_html/<SPACE_KEY>/<pageid>.html
        │
        ▼
  page_parser.py        ──── BeautifulSoup + markdownify ──── structured sections
        │
        ▼
  data/raw/<SPACE_KEY>/<pageid>_<title>.json
        │
        ├── markdown_writer.py ──── data/docupedia_data_page/<SPACE_KEY>/<pageid>_<title>.md
        │
        ▼
  chunker.py + embedding_model.py (sentence-transformers)
        │
        ▼
  chroma_store.py ──── data/chroma_db/
        │
        ▼
  mcp_server/server.py ──── GitHub Copilot @Docupedia agent
```

**Authentication:** PAT is read from `.env` and sent automatically as a `Bearer` token on every Confluence API request. No explicit login step is required.

---

## Prerequisites

- Python 3.10+
- **PX Proxy** must be running (corporate proxy on `http://127.0.0.1:3128`)
- A Docupedia / Confluence Personal Access Token (PAT)

---

## Setup Guide

### 1. Activate the virtual environment

```powershell
# Create venv (first time only)
python -m venv .venv

# Activate on Windows
.venv\Scripts\activate
```

> Always activate the virtual environment before running any commands. You should see `(.venv)` in your prompt.

### 2. Start PX Proxy

Make sure **PX Proxy** is running on `http://127.0.0.1:3128` before running the pipeline if you are inside the corporate network.

### 3. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

> **Corporate network (PX Proxy):** If direct internet access is blocked, start PX Proxy first and install via:
> ```powershell
> python -m pip install --proxy http://127.0.0.1:3128 -r requirements.txt
> ```

### 4. Configure environment variables

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

```env
# Your Docupedia / Confluence Personal Access Token
DOCUPEDIA_PAT=your_personal_access_token_here

# Base URL of your Confluence instance (no trailing slash)
DOCUPEDIA_BASE_URL=https://inside-docupedia.bosch.com/confluence2

# Confluence space key to crawl (e.g. BBM, ~username, TEAM)
SPACE_KEY=YOUR_SPACE_KEY

# Max pages to crawl — set to 0 to crawl ALL pages in the space
MAX_PAGES=0
```

> The PAT is sent as a `Bearer` token automatically. No login function needed.

### 5. (Optional) Download the embedding model for offline use

By default, `sentence-transformers` downloads `intfloat/multilingual-e5-base` (~280 MB) from HuggingFace on the first `embed` run. On machines with **no internet access**, this fails and the embed step crashes.

**On the machine with internet access**, run the download helper once:

```powershell
python scripts/download_model.py
```

This saves the model to `data/models/multilingual-e5-base/`.

**Copy the project** (including `data/models/`) to the offline laptop, then add this line to `.env` on that machine:

```env
EMBEDDING_MODEL=data/models/multilingual-e5-base
```

`config.py` detects the local directory automatically and loads the model from disk — no network calls are made.

> `data/` is in `.gitignore`, so the model folder is never committed to git. Copy it manually or via a shared network drive.

---

## Running the Pipeline

### Full pipeline (recommended)

```powershell
python pipeline.py run
```

### Step by step

```powershell
# Step 1 — Crawl: fetch pages, download attachments, save HTML + JSON + Markdown
python pipeline.py crawl

# Step 2 — Embed: chunk JSON files and upsert into ChromaDB
python pipeline.py embed

# Test with a limited number of pages first
python pipeline.py crawl --limit 5
```

> `embed` is idempotent — re-running it updates existing chunks in place, no duplicates.

### Crawl a specific page and all its sub-pages

Instead of crawling the entire space, you can target a single root page by ID. The pipeline will crawl that page and every descendant recursively.

**Option A — via `.env`** (persists across runs):
```env
PAGE_ID=2155921768
```
Then run normally:
```powershell
python pipeline.py run
```

**Option B — via CLI argument** (one-off, overrides `.env`):
```powershell
python pipeline.py crawl --page-id 2155921768
python pipeline.py run   --page-id 2155921768   # crawl subtree + embed
```

Leave `PAGE_ID` empty (or unset) to crawl the entire space as usual.

### Crawling multiple Confluence spaces

To add a second space to the same knowledge base:

1. Change `SPACE_KEY=SECONDSPACE` in `.env`
2. Run `python pipeline.py run`
3. New pages are upserted alongside existing ones — all spaces are searchable together

Confluence page IDs are globally unique across all spaces, so there is no risk of ID collision.

---

## GitHub Copilot Agent

Once the pipeline has been run at least once, the `@Docupedia` agent is available in VS Code Copilot Chat.

VS Code automatically starts the MCP server in the background when the workspace opens (configured in `.vscode/mcp.json`). No manual setup needed.

### Tools

| Tool | What it does |
|---|---|
| `search_docs` | Semantic vector search — finds the most relevant chunks for a query |
| `get_page` | Returns the full content of a specific page by ID |
| `list_pages` | Lists all pages in the local knowledge base with IDs and URLs |

### Usage

In VS Code Copilot Chat, select **Docupedia** from the agent picker:

```
@Docupedia What is the BBM Data Architecture?
@Docupedia List all pages about data governance
```

The agent answers **only** from crawled Docupedia content and always cites page titles and URLs. Queries in Vietnamese, English, or German are all supported.

---

## Quick-start Checklist

- [ ] Virtual environment activated (`.venv\Scripts\Activate.ps1`)
- [ ] PX Proxy is running on `http://127.0.0.1:3128`
- [ ] Dependencies installed: `python -m pip install --proxy http://127.0.0.1:3128 -r requirements.txt`
- [ ] `.env` created and populated with `DOCUPEDIA_PAT`, `DOCUPEDIA_BASE_URL`, `SPACE_KEY`
- [ ] Pipeline run at least once: `python pipeline.py run`
- [ ] Reload VS Code — MCP server starts automatically, `@Docupedia` agent is ready

---

## Configuration Reference (`config.py`)

| Variable | Default | Description |
|---|---|---|
| `DOCUPEDIA_PAT` | *(required)* | Personal Access Token — Bearer token on all API requests |
| `DOCUPEDIA_BASE_URL` | *(required)* | Base URL of the Confluence instance (no trailing slash) |
| `SPACE_KEY` | *(required)* | Confluence space key to crawl |
| `MAX_PAGES` | `0` (all) | Max pages to crawl; `0` = no limit; override via `--limit` flag |
| `REQUEST_RETRIES` | `3` | HTTP retry attempts on transient failures |
| `REQUEST_TIMEOUT` | `30` | HTTP request timeout in seconds |
| `REQUEST_DELAY` | `0.5` | Base delay between requests (seconds) |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` | HuggingFace model ID **or** local directory path (relative to project root) — use a local path for offline machines |
| `EMBEDDING_DIMENSION` | `768` | Output vector dimension |
| `CHUNK_SIZE` | `512` | Token size per text chunk |
| `CHUNK_OVERLAP` | `64` | Token overlap between adjacent chunks |
| `CHROMA_COLLECTION_NAME` | `docupedia` | ChromaDB collection name |
