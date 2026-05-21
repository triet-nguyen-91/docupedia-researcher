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
│       └── docupedia.agent.md     # Ask Docupedia custom chat agent definition
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
      mcp_server/server.py ──── GitHub Copilot Ask Docupedia agent
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

# Confluence space key to crawl and store under data/.../<SPACE_KEY>/
SPACE_KEY=YOUR_SPACE_KEY

# Optional: search scope for Copilot MCP and Chat UI retrieval.
# Leave empty to search all indexed spaces.
# Set one or more comma-separated space keys to restrict search results.
SPACE_TARGET=

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

### Controlling search scope with `SPACE_TARGET`

`SPACE_KEY` controls which space is crawled and where files are written locally. `SPACE_TARGET` controls which spaces are searched by the MCP server and the optional Chat UI.

Examples:

```env
# Search every indexed space
SPACE_TARGET=

# Search one space only
SPACE_TARGET=BBMRL

# Search multiple spaces only
SPACE_TARGET=BBMRL,BBMDATAARCHITECTURE
```

If you are upgrading from an older index that was created before `space_key` metadata existed, run `python pipeline.py sync-metadata` once for each previously crawled `SPACE_KEY` so targeted search can filter correctly.

---

## GitHub Copilot Agent

Once the pipeline has been run at least once, the **Ask Docupedia** agent is available in VS Code Copilot Chat.

VS Code automatically starts the MCP server in the background when the workspace opens (configured in `.vscode/mcp.json`). No manual setup needed.

### Tools

| Tool | What it does |
|---|---|
| `search_docs` | Semantic vector search — finds the most relevant chunks for a query |
| `get_page` | Returns the full content of a specific page by ID |
| `list_pages` | Lists all pages in the local knowledge base with IDs and URLs |

### Usage

In VS Code Copilot Chat, select **Ask Docupedia** from the agent picker, then ask your question:

```
What is the BBM Data Architecture?
List all pages about data governance
```

The agent answers **only** from crawled Docupedia content and always cites page titles and URLs. Queries in Vietnamese, English, or German are all supported.

---

## Optional: Chat UI (Chainlit + Ollama)

The default way to query the knowledge base is via the **GitHub Copilot Ask Docupedia agent** (see above). No extra setup is needed for that.

If you want a **standalone browser-based chat UI** instead, you can use the optional Chainlit + Ollama integration. This is completely independent of VS Code and GitHub Copilot.

### How it works

```
User question (browser)
        │
        ▼
  chat_ui/app.py  ─── embed query ──▶  ChromaDB (same data as Copilot agent)
        │                                     │
        │            top-K chunks ◀───────────┘
        ▼
  Ollama (local LLM — e.g. llama3.2)
        │
        ▼
  Streamed answer + source citations (browser)
```

### Prerequisites
0. **Install Ollama cli**:

   ```powershell
   irm https://ollama.com/install.ps1 | iex
   ```

1. **Ollama** — install from [https://ollama.com](https://ollama.com) and pull a model:

   ```powershell
   # Pull a model (once, ~2–4 GB depending on model)
   ollama pull llama3.2
   ```

2. Run the main pipeline at least once so ChromaDB is populated:

   ```powershell
   python pipeline.py run
   ```

### Install Chat UI dependencies

```powershell
python -m pip install -r requirements-chat.txt
# or with PX Proxy:
python -m pip install --proxy http://127.0.0.1:3128 -r requirements-chat.txt
```

### Configure (optional)

Add these lines to your `.env` (sensible defaults are already set):

```env
OLLAMA_MODEL=llama3.2          # any model you pulled with `ollama pull`
OLLAMA_BASE_URL=http://localhost:11434
CHAT_TOP_K=6                   # final chunks sent to the model
CHAT_SEARCH_K=14               # fetch more candidates, then rerank and deduplicate
CHAT_MAX_CONTEXT_CHARS=7000    # cap prompt size so the model stays focused
CHAT_MAX_HISTORY_TURNS=2       # helps follow-up questions without bloating context
CHAT_MAX_CHUNKS_PER_PAGE=2     # avoid overloading the prompt with one page only
OLLAMA_TEMPERATURE=0.1         # lower = more factual / less chatty
OLLAMA_NUM_CTX=8192            # larger context window for retrieved chunks
```

Recommended quality-first settings:

- Use a stronger model than `llama3.2` when possible. Good local options are `qwen2.5:7b`, `qwen3:8b`, or `llama3.1:8b` if your machine can handle them.
- Keep `OLLAMA_TEMPERATURE` low for RAG. Higher temperature makes local models sound fluent, but usually hurts factual accuracy.
- Increase `CHAT_SEARCH_K` before increasing `CHAT_TOP_K`. It is usually better to fetch more candidates and rerank them than to dump many raw chunks straight into the prompt.

### Start the Chat UI

```powershell
# Terminal 1 — Ollama server (keep running)
ollama serve

# Terminal 2 — Chainlit UI
chainlit run chat_ui/app.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

> **Note:** The Chat UI and the GitHub Copilot agent share the same ChromaDB vector store. There is no need to re-embed if you have already run `python pipeline.py embed`.
