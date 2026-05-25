# Setup Guide

## Prerequisites

- Python 3.10+
- PX Proxy running on `http://127.0.0.1:3128` when required by your network
- A Docupedia / Confluence Personal Access Token

## 1. Create And Activate The Virtual Environment

```powershell
python -m venv .venv
.venv\Scripts\activate
```

## 2. Install Base Dependencies

```powershell
python -m pip install -r requirements.txt
```

If you need PX Proxy:

```powershell
python -m pip install --proxy http://127.0.0.1:3128 -r requirements.txt
```

## 3. Configure `.env`

```powershell
Copy-Item .env.example .env
```

Minimum required values:

```env
DOCUPEDIA_PAT=your_personal_access_token_here
DOCUPEDIA_BASE_URL=https://inside-docupedia.bosch.com/confluence2
SPACE_KEY=YOUR_SPACE_KEY
SPACE_TARGET=
MAX_PAGES=0
```

Notes:

- `SPACE_KEY` controls which space is crawled and where local files are stored.
- `SPACE_TARGET` controls which indexed spaces are searched by MCP and the optional Chat UI.
- The PAT is sent automatically as a Bearer token on every request.

## 4. Optional Offline Embedding Model

If the machine cannot reach HuggingFace, download the embedding model on a connected machine first:

```powershell
python scripts/download_model.py
```

Then copy `data/models/` and set:

```env
EMBEDDING_MODEL=data/models/multilingual-e5-base
```

## 5. Run The Pipeline

Full run:

```powershell
python pipeline.py run
```

Step by step:

```powershell
python pipeline.py crawl
python pipeline.py embed
```

Test run:

```powershell
python pipeline.py crawl --limit 5
```

## 6. Crawl A Specific Root Page

Via `.env`:

```env
PAGE_ID=2155921768
```

Or one-off via CLI:

```powershell
python pipeline.py crawl --page-id 2155921768
python pipeline.py run --page-id 2155921768
```

## 7. Multiple Spaces

To add another space into the same ChromaDB:

1. Change `SPACE_KEY`.
2. Run `python pipeline.py run` again.
3. Adjust `SPACE_TARGET` if you want to limit retrieval to selected spaces.

## 8. Search Scope

Examples:

```env
SPACE_TARGET=
SPACE_TARGET=BBMRL
SPACE_TARGET=BBMRL,BBMDATAARCHITECTURE
```

If old indexed chunks do not yet contain `space_key` metadata, run:

```powershell
python pipeline.py sync-metadata
```

Run that once per previously indexed `SPACE_KEY`.

## 9. GitHub Copilot MCP Usage

After at least one successful pipeline run, the Ask Docupedia agent can query the indexed knowledge base.

The MCP server starts automatically from `.vscode/mcp.json` when the workspace opens in VS Code.

Typical usage:

```text
What is the BBM Data Architecture?
List all pages about data governance
```

## Operational Notes

- `embed` is idempotent.
- Crawls are resume-safe and skip unchanged pages.
- Confluence page IDs are globally unique, so multiple spaces can share one ChromaDB store.
- The default user flow is GitHub Copilot, not the Chat UI.