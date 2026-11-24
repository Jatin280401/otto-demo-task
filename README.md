# Otto Live — meeting copilot demo

## What it does

Otto Live ingests live meeting audio (or a uploaded/streamed file) and, in real time,
answers questions in the meeting chat by grounding claims in:

1. **The live transcript** — what people have actually said so far, with timestamps.
2. **The project knowledge base** — TF‑IDF‑based retrieval from the indexed PDF(s),
   with page-number citations.

No API keys are required. The demo uses:

- **faster-whisper** (base.en, CPU int8) for speech‑to‑text, streaming 100 ms PCM chunks.
- **Ollama** (qwen2.5:1.5b-instruct) as the LLM engine; a deterministic “grounded”
  extractive composer is the fallback when the local model cannot answer.
- **TF‑IDF** over the EarnOut handover manual (154 pages, 1478 chunks) for PDF retrieval.

## Quick start

```bash
# 1. Clone / cd into the project
cd demo-task-2

# 2. Create and activate the venv (already present at .venv)
source .venv/bin/activate

# 3. Install deps
pip install -r requirements.txt

# 4. Start Ollama (if not already running)
ollama serve

# 5. Pull the model (first time only)
ollama pull qwen2.5:1.5b-instruct

# 6. Start the server
./run.sh          # or: uvicorn app.main:app --host 127.0.0.1 --port 8000

# 7. Open http://127.0.0.1:8000 in a browser
```

## Development scripts

- `./run.sh` — starts uvicorn with the app and prints startup diagnostics.
- `make_sample_audio.py` (in `scripts/`) — generates the synthetic two‑speaker
  meeting WAV/MP3 and its ground‑truth transcript used for demos.
- `scripts/build_kb_pdf.py` — converts the Task 1 docx into the PDF knowledge base.

## Architecture

| Component          | Role |
|--------------------|------|
| `app/main.py`      | FastAPI app: routes, WebSocket `/ws/meetings/{id}`, ASR wiring, KB ingest/search, Meet bridge, static UI serving. |
| `app/orchestrator.py` | Retrieval → prompt → LLM streaming → deterministic grounded composer; citations; ambient proactive cues; recap. |
| `app/kb.py`        | PDF chunking, TF‑IDF hybrid retrieval, query expansion, best‑sentence extraction. |
| `app/llm.py`       | Ollama/OpenAI streaming client; model detection; warmup to avoid first‑request latency. |
| `app/models.py`    | Pydantic/schemas: ChatMessage, Evidence, KbChunk, Timings, TranscriptSegment. |
| `app/prompts.py`   | Prompt rendering: system prompt, answer template, ambient cue, recap. |
| `app/audio.py`     | Audio streamer (ffmpeg‑based file/URL streaming; MediaStream mic capture). |
| `app/asr.py`       | Streaming Whisper ASR with alignment fallback. |
| `app/meeting.py`   | MeetingSession, MeetingStore, broadcast helpers. |
| `app/config.py`    | Environment settings (`OTTO_*`) and path configuration. |
| `app/google_meet.py` | Meet code parsing, caption helper, Google Chat webhook. |
| `extension/`       | Chrome MV3 extension: content script polls Meet captions DOM, posts to `/api/meet/captions`; optional overlay. |
| `knowledge/earnout-handover.pdf` | 154‑page PDF derived from the Task 1 handover manual; 1478 TF‑IDF chunks. |
| `data/sample_meeting.wav` / `.mp3` / `.transcript.json` | Synthetic 2‑speaker audio (≈2½ min) and its ground‑truth transcript. |

## UI

The front‑end (`app/static/`) is vanilla HTML/JS/CSS — no build step required.

- **Left panel**: meeting audio visualiser, live transcript with speaker tags, and a 90‑second rolling window.
- **Center panel**: meeting chat with Otto’s grounded answers, evidence citations (`[meeting mm:ss]`, `[KB p.N]`), and quick‑question buttons.
- **Right panel**: inspector showing the exact transcript lines and PDF passages used, the rendered prompt, latency breakdown, and KB search.
- **Bottom**: toast notifications, ambient‑note toggle, and recap modal.

## Google Meet integration

Google Meet has no public “join call” or “post to in‑call chat” API. The demo provides two paths:

1. **Chrome MV3 extension** (`extension/`): when installed in Chrome, the extension
   runs on `meet.google.com`, reads the (limited) caption DOM, and POSTs each caption
   to `POST /api/meet/captions` in the Otto backend. The backend injects the caption
   as a transcript segment, and Otto can then answer from it.

2. **Optional Google Chat webhook**: set `GOOGLE_CHAT_WEBHOOK_URL` in `.env` and
   Otto will POST each answer to the linked Chat space.

For production audio, use the Google Meet Media API (Workspace developer preview) or
a recording bot with consent.

## Environment variables

Copy `.env.example` and adjust as needed:

```
# LLM
OTTO_LLM=auto           # auto | ollama | openai | grounded
OTTO_OLLAMA_MODEL=qwen2.5:1.5b-instruct
OTTO_OLLAMA_BASE_URL=http://127.0.0.1:11434

# ASR
OTTO_ASR=auto           # auto | whisper | alignment | off
OTTO_ASR_MODEL=base.en
OTTO_ASR_COMPUTE_TYPE=int8
OTTO_ASR_DEVICE=auto

# Retrieval / meeting
OTTO_TRANSCRIPT_WINDOW_S=90.0
OTTO_KB_TOP_K=5
OTTO_KB_CHUNK_CHARS=700
OTTO_CONTEXT_MAX_CHARS=26000

# Ambient
OTTO_AMBIENT=true
OTTO_AMBIENT_COOLDOWN_S=25.0
OTTO_AMBIENT_QUIET_S=3.0

# Knowledge base
OTTO_KB_PDF=knowledge/earnout-handover.pdf
OTTO_KB_EXTRA_PDFS=

# Integrations
OTTO_ALLOW_PRIVATE_URLS=false
GOOGLE_CHAT_WEBHOOK_URL=

# Runtime
OTTO_PUBLIC_URL=http://localhost:8000
OTTO_SAMPLE_AUDIO=data/sample_meeting.wav
OTTO_SAMPLE_TRANSCRIPT=data/sample_meeting.transcript.json
```

## Tests

A minimal test suite lives in `tests/`. Run with:

```bash
source .venv/bin/activate
pytest tests/ -v
```

Tests cover:
- KB search and best‑sentence extraction.
- Prompt rendering (system, answer, ambient, recap).
- Orchestrator citation cleanup and grounding.
- Config defaults and path resolution.
```

## Browser demo

Open http://127.0.0.1:8000 and:

- Click **Run live demo** to stream the sample meeting through ASR.
- Use **Quick questions** (Who owns Otto? etc.) to see Otto answer from live transcript + PDF.
- Toggle **Proactive notes** to see ambient cues.
- Click **Recap** to generate a summary of the meeting so far.
- Install the Chrome extension from `extension/` to bridge live Google Meet captions.

## What's included

- `app/` — all Python source (≈12 files, ~8 KB each).
- `knowledge/earnout-handover.pdf` — 154‑page knowledge base.
- `data/sample_meeting.wav` / `.mp3` / `.transcript.json` — demo audio + transcript.
- `extension/` — Chrome MV3 extension for Google Meet caption bridge.
- `prompts/` — four markdown prompt files (system, answer, ambient, recap).
- `scripts/` — utility scripts (PDF build, sample audio generation).
- `requirements.txt` — pinned dependencies.
- `README.md` — this file.

## Roadmap (future)

- Swap TF‑IDF for a lightweight sentence‑embedding model (all-MiniLM-L6‑v2) without
  changing the prompt contract.
- Add embedding-based KB retrieval as an optional backend.
- Support `OTTO_ASR_DEVICE=mps` for Apple‑Silicon acceleration.
- Add a proper Meet “join” flow using the Google Meet Media API (preview).
- Add a proper pytest suite with integration tests against a running server.