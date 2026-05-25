# Optional Chat UI

The Chat UI is optional. The default path for this repo is GitHub Copilot + MCP.

This UI uses Chainlit for the frontend and Ollama as the local LLM backend, while reusing the same ChromaDB index as the Copilot path.

## Prerequisites

1. Base project setup is already complete.
2. ChromaDB already contains indexed content.
3. Ollama is installed locally.

Install Ollama if needed:

```powershell
irm https://ollama.com/install.ps1 | iex
```

Pull a model:

```powershell
ollama pull llama3.2
```

## Install Chat UI Dependencies

```powershell
python -m pip install -r requirements-chat.txt
```

With PX Proxy:

```powershell
python -m pip install --proxy http://127.0.0.1:3128 -r requirements-chat.txt
```

## Chat UI Environment Variables

Add or tune these values in `.env`:

```env
OLLAMA_MODEL=llama3.2
OLLAMA_BASE_URL=http://localhost:11434
CHAT_TOP_K=6
CHAT_SEARCH_K=14
CHAT_MAX_CONTEXT_CHARS=7000
CHAT_MAX_HISTORY_TURNS=2
CHAT_MAX_CHUNKS_PER_PAGE=2
OLLAMA_TEMPERATURE=0.1
OLLAMA_TOP_P=0.9
OLLAMA_REPEAT_PENALTY=1.1
OLLAMA_NUM_CTX=8192
OLLAMA_NUM_PREDICT=768
```

## Start The UI

Terminal 1:

```powershell
ollama serve
```

Terminal 2:

```powershell
chainlit run chat_ui/app.py
```

Open `http://localhost:8000` in the browser.

## Quality Tuning

- Prefer a stronger local model than `llama3.2` when hardware allows.
- Keep temperature low for RAG use cases.
- Increase `CHAT_SEARCH_K` before increasing `CHAT_TOP_K`.
- `CHAT_MAX_CHUNKS_PER_PAGE=2` helps keep context diverse.

Good candidate models:

- `qwen2.5:7b`
- `qwen3:8b`
- `llama3.1:8b`

## Notes

- The Chat UI and GitHub Copilot share the same ChromaDB store.
- Ollama is not started by `chat_ui/app.py`; it must already be running.
- `ollama stop` stops a loaded model, not the Ollama server process itself.