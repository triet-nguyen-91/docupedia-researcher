# BBM Docupedia RAG Pipeline

RAG pipeline for Docupedia and Confluence content.

The default user path is the GitHub Copilot Ask Docupedia agent via the local MCP server. An optional Chainlit + Ollama Chat UI is available as a separate setup.

## What This Repo Does

- Crawls a Confluence space or subtree with PAT authentication
- Downloads page attachments and rewrites HTML to local offline files
- Parses page HTML into structured sections
- Chunks and embeds content into ChromaDB
- Exposes the indexed knowledge base to GitHub Copilot through MCP
- Optionally exposes the same ChromaDB through a browser Chat UI

## Project Backbone

- [config.py](config.py): central configuration and environment loading
- [pipeline.py](pipeline.py): crawl, embed, run, and maintenance commands
- [crawler/](crawler): Confluence fetch, parsing, and attachment download
- [processor/](processor): chunking and Markdown export
- [embedder/](embedder): embeddings and ChromaDB access
- [mcp_server/](mcp_server): MCP server for GitHub Copilot
- [chat_ui/](chat_ui): optional Chainlit + Ollama UI

## Data Layout

All crawl output is scoped by `SPACE_KEY`.

- `data/raw/<SPACE_KEY>/`: parsed JSON pages
- `data/raw_html/<SPACE_KEY>/`: offline HTML pages
- `data/images/<SPACE_KEY>/`: downloaded attachments
- `data/docupedia_data_page/<SPACE_KEY>/`: Markdown export
- `data/chroma_db/`: shared ChromaDB store

## Quick Start

1. Follow the full setup guide in [docs/setup.md](docs/setup.md).
2. Run `python pipeline.py run`.
3. Open VS Code Copilot Chat and use the Ask Docupedia agent.

## Documentation

- [docs/setup.md](docs/setup.md): environment, installation, `.env`, crawl, embed, and MCP usage
- [docs/chat-ui.md](docs/chat-ui.md): optional Chainlit + Ollama Chat UI setup and tuning

## Default Query Path

GitHub Copilot + MCP is the default setup. The Chat UI is optional and installed separately.
