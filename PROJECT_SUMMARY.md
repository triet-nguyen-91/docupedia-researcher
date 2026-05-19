# Docupedia RAG Pipeline — Project Summary

---

### 1. Project Overview

The **Docupedia RAG Pipeline** is an end-to-end Retrieval-Augmented Generation (RAG) system purpose-built for querying an internal Confluence knowledge base (Docupedia). It crawls a configured Confluence space using a Personal Access Token, processes and stores all page content in a local vector database (ChromaDB), and exposes the indexed knowledge to **GitHub Copilot** via a local MCP (Model Context Protocol) server. The result is a conversational AI agent that can answer natural-language questions — in English, German, or Vietnamese — against the full body of internal documentation.

---

### 2. The Problem It Solves

Internal Confluence/Docupedia spaces contain large amounts of institutional knowledge (architecture decisions, process guidelines, use-case definitions, quality assurance procedures, etc.), but browsing or searching them manually is slow and imprecise. This project addresses several concrete pain points:

- **Knowledge discoverability**: Finding the right page among hundreds requires knowing exactly what to search for; semantic vector search lets you ask a question naturally.
- **Copilot context gap**: GitHub Copilot has no awareness of internal documentation. Without this pipeline, developers must manually look up pages and paste context into the chat.
- **Multilingual friction**: Documentation exists in English and German, but developers may prefer to ask questions in Vietnamese or English. The cross-lingual embedding model removes this barrier.
- **Offline resilience**: Raw HTML and attachments are saved locally, so the pipeline can re-process content without re-crawling the live Confluence server.
- **Staleness**: The pipeline is resume-safe and idempotent — re-running it skips already-crawled pages and upserts only changed chunks.

---

### 3. Core Components & Architecture

#### Components

| Component | Location | Responsibility |
|---|---|---|
| **Config** | `config.py` | Single source of truth for all paths, credentials, and tuning parameters. Loaded from `.env`. |
| **Confluence Client** | `crawler/confluence_client.py` | Authenticates via PAT Bearer token; paginates through the Confluence space and fetches all page HTML, metadata, and labels in batched API calls. Includes automatic retry with exponential back-off. |
| **Image Downloader** | `crawler/image_downloader.py` | Downloads all page attachments (images, videos, files) to `data/images/<SPACE_KEY>/<pageid>/` and rewrites `src`/`href` attributes in the raw HTML to local file paths to create a fully offline archive. |
| **Page Parser** | `crawler/page_parser.py` | Uses BeautifulSoup to split Confluence HTML at heading boundaries (H1–H6) and converts each section to clean Markdown via `markdownify`, producing a structured `{"sections": [...]}` dict. |
| **Markdown Writer** | `processor/markdown_writer.py` | Writes each parsed page to `data/docupedia_data_page/<SPACE_KEY>/` as a Markdown file with YAML frontmatter for human-readable offline reference. |
| **Chunker** | `processor/chunker.py` | Splits each page's sections into 512-token chunks (with 64-token overlap) using LangChain's `RecursiveCharacterTextSplitter`. Each chunk carries full metadata (page ID, title, section heading, URL, last modified). |
| **Embedding Model** | `embedder/embedding_model.py` | Lazy-loads `intfloat/multilingual-e5-base` (768-dim, ~280 MB) via `sentence-transformers`. Applies `"passage: "` prefix for indexing and `"query: "` prefix for retrieval, as required by the E5 model family. |
| **Chroma Store** | `embedder/chroma_store.py` | Manages a persistent ChromaDB collection using cosine similarity. Provides `upsert_chunks()` (idempotent, batched) and `query()` (semantic search). |
| **MCP Server** | `mcp_server/server.py` | A FastMCP stdio server started automatically by VS Code (via `.vscode/mcp.json`). Exposes three tools to GitHub Copilot: `search_docs`, `get_page`, and `list_pages`. |
| **Pipeline CLI** | `pipeline.py` | Orchestrates all steps via `argparse` subcommands: `crawl`, `embed`, and `run` (both). |

#### Data Flow

```
Confluence API (live)
        │
        │  PAT Bearer auth, batch GET /rest/api/content?expand=body.view,...
        ▼
[1. Confluence Client]  ──► iter_all_pages()
        │
        │  raw page dict {pageid, title, html, labels, ...}
        ▼
[2. Image Downloader]   ──► download_page_images()
        │
        │  offline_html (all src/href → local paths)
        │  downloads to: data/images/<SPACE_KEY>/<pageid>/
        ▼
[3. Page Parser]        ──► parse_page()
        │
        │  structured dict {pageid, title, url, sections: [{heading, level, text},…]}
        ▼
[4. Markdown Writer]    ──► write_page_markdown()   → data/docupedia_data_page/<SPACE_KEY>/
[4. JSON Save]          ──► json.dump()             → data/raw/<SPACE_KEY>/<pageid>_<title>.json
[4. HTML Save]                                      → data/raw_html/<SPACE_KEY>/<pageid>.html
        │
        │  (embed step reads from data/raw/ JSON)
        ▼
[5. Chunker]            ──► chunk_page()
        │
        │  list of {id, text, metadata} chunks  (512 tokens, 64 overlap)
        ▼
[6. Embedding Model]    ──► embed_documents()   (passage: prefix → E5 768-dim vectors)
        │
        ▼
[7. Chroma Store]       ──► upsert_chunks()   → data/chroma_db/  (persistent, cosine)
        │
        │  (at query time)
        ▼
[8. MCP Server]         ──► search_docs(query)
        │               ──► embed_query()  (query: prefix → E5 vector)
        │               ──► ChromaDB cosine ANN search
        ▼
GitHub Copilot Chat  ◄──  top-k results with title, section, URL, relevance score
```

---

### 4. Technology Stack

| Category | Technology | Purpose |
|---|---|---|
| **Language** | Python 3.10+ | Entire pipeline and server |
| **Confluence API** | `atlassian-python-api` | Paginated REST API access to Docupedia |
| **HTTP** | `requests` | Underlying HTTP transport; session-level PAT auth |
| **HTML Parsing** | `beautifulsoup4` | Splitting Confluence HTML at heading boundaries |
| **Markdown Conversion** | `markdownify` | Converting HTML section bodies to clean Markdown |
| **Text Splitting** | `langchain-text-splitters` | `RecursiveCharacterTextSplitter` for token-aware chunking |
| **Embedding Model** | `sentence-transformers` + `intfloat/multilingual-e5-base` | 768-dim cross-lingual embeddings; supports EN / DE / VI |
| **Deep Learning** | `torch`, `transformers`, `accelerate` | Model inference backend |
| **Vector Store** | `chromadb` | Persistent local vector DB with cosine similarity (HNSW index) |
| **MCP Server** | `mcp[cli]` + `FastMCP` | Stdio-based Model Context Protocol server for VS Code / Copilot |
| **Configuration** | `python-dotenv` | Loads secrets from `.env` at startup |
| **CLI** | `argparse` | `crawl`, `embed`, `run` subcommands in `pipeline.py` |
| **Progress Display** | `tqdm` | Per-page progress bars during crawl and embed steps |
| **Image Handling** | `Pillow` | Image processing for downloaded attachments |
| **Proxy** | PX Proxy (`http://127.0.0.1:3128`) | Required for `pip install` and outbound Confluence API calls in the network |

---

### 5. How to Run the Project

#### Prerequisites

- Python 3.10 or higher
- Access to the internal network (PX Proxy on `127.0.0.1:3128`)
- A Confluence Personal Access Token (PAT) for Docupedia

#### Step 1 — Create the environment file

Copy the example and fill in your credentials:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set:

```env
DOCUPEDIA_PAT=<your-personal-access-token>
DOCUPEDIA_BASE_URL=https://<your-confluence-host>
SPACE_KEY=BBMDATAARCHITECTURE
MAX_PAGES=0   # 0 = no limit; set a small number (e.g. 5) for testing
```

#### Step 2 — Create and activate the virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

#### Step 3 — Install dependencies

> The PX Proxy is required for package downloads inside the network.

```powershell
pip install --proxy http://127.0.0.1:3128 -r requirements.txt
```

#### Step 4 — Run the pipeline

**Option A — Full pipeline (crawl + embed in one shot):**

```powershell
python pipeline.py run
```

**Option B — Steps separately:**

```powershell
python pipeline.py crawl        # Fetch pages → HTML + JSON + Markdown
python pipeline.py embed        # Chunk + embed → ChromaDB
```

**Test with a small page limit:**

```powershell
python pipeline.py crawl --limit 5
```

#### Step 5 — Use in GitHub Copilot

VS Code automatically starts the MCP server when the workspace is opened (configured in `.vscode/mcp.json`). Once the pipeline has been run at least once, open Copilot Chat and use the **Data Hub** agent or simply ask questions — Copilot will call `search_docs` to retrieve relevant internal documentation automatically.

To start the MCP server manually:

```powershell
python -m mcp_server.server
```
