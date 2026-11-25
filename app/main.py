from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
import numpy as np
from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .asr import build_asr, preload_whisper, whisper_status
from .audio import AudioStreamer, resolve_local_audio
from .config import DATA_DIR, PROMPTS_DIR, STATIC_DIR, UPLOAD_DIR, settings
from .kb import kb
from .llm import LLMClient
from .meeting import AudioState, MeetingSession, store
from .models import (
    ChatMessage,
    ChatRequest,
    CreateMeetingRequest,
    KbIngestUrlRequest,
    KbSearchRequest,
    TranscriptSegment,
    new_id,
)
from .orchestrator import Orchestrator

llm = LLMClient()
orchestrator = Orchestrator(llm)
asrs: dict[str, object] = {}
streamers: dict[str, AudioStreamer] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(kb.load_defaults)
    await llm.detect()
    await preload_whisper()
    asyncio.create_task(llm.warmup())
    print("\n  Otto Live")
    print(f"  LLM     : {llm.info.engine} / {llm.info.model} ({llm.info.detail})")
    print(f"  ASR     : {whisper_status()}")
    print(f"  KB      : {kb.status()['chunks']} chunks · {kb.status()['pages']} pages")
    print(f"  Prompt  : {settings.prompt_version}\n")
    yield
    for streamer in list(streamers.values()):
        await streamer.stop()
    for asr in list(asrs.values()):
        await asr.stop()

app = FastAPI(title="Otto Live", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/demo", StaticFiles(directory=str(DATA_DIR)), name="demo")

def runtime_info() -> dict:
    return {
        "llm": {
            "engine": llm.info.engine,
            "model": llm.info.model,
            "available": llm.info.available,
            "detail": llm.info.detail,
        },
        "asr": whisper_status(),
        "kb": kb.status(),
        "prompt_version": settings.prompt_version,
        "transcript_window_s": settings.transcript_window_s,
        "audio_chunk_ms": 100,
        "partial_interval_ms": settings.partial_interval_ms,
    }

async def ensure_asr(session: MeetingSession, use_speaker_map: bool = True) -> object:
    existing = asrs.get(session.id)
    if existing:
        return existing

    async def on_partial(text: str, start: float, end: float, lag: Optional[int]):
        session.set_partial(text, start)
        await session.emit({
            "type": "transcript.partial",
            "text": text,
            "t_start": round(start, 2),
            "t_end": round(end, 2),
            "asr_lag_ms": lag,
        })

    async def on_final(segment: TranscriptSegment):
        added = session.add_segment(segment)
        if added:
            await session.emit({"type": "transcript.segment", "segment": segment.to_dict()})
            orchestrator.schedule_ambient(session)

    asr = build_asr(session, on_partial, on_final, use_speaker_map=use_speaker_map)
    asrs[session.id] = asr
    await asr.start()
    return asr

async def stop_audio(session: MeetingSession, stop_asr: bool = True) -> None:
    streamer = streamers.pop(session.id, None)
    if streamer:
        await streamer.stop()
    if stop_asr:
        asr = asrs.pop(session.id, None)
        if asr:
            await asr.stop()
        session.audio.status = "stopped"
        session.audio.ended_at = time.time()
        session.audio.offset_s = round(session.audio.clock, 2)

async def broadcast_message(session: MeetingSession, message: object) -> None:
    await session.emit({"type": "chat.message", "message": message.to_dict()})

async def add_user_message(session: MeetingSession, text: str) -> object:
    msg = object.__new__(object)  
    session.add_message(msg)
    await session.emit({"type": "chat.message", "message": str(msg)})
    return msg

def validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(400, "Only http(s) audio URLs are supported")
    if not parsed.hostname:
        raise HTTPException(400, "URL has no host")
    if settings.kb_allow_private_urls:
        return
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise HTTPException(400, "Private/loopback URLs are blocked")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not resolve URL host: {exc}")

@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/api/health")
async def health():
    return {"status": "healthy", "time": time.time()}

@app.get("/api/config")
async def config():
    return runtime_info()

@app.post("/api/meetings")
async def create_meeting(req: CreateMeetingRequest):
    if req.meeting_code:
        session = store.ensure_for_code(req.meeting_code, req.title)
        return session.info().model_dump()
    session = store.create(req.title, source=req.source)
    return session.info().model_dump()

@app.get("/api/meetings/{session_id}")
async def get_meeting(session_id: str):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Meeting not found")
    return session.public_state()

@app.get("/api/meetings/{session_id}/messages")
async def get_messages(session_id: str, since: float = Query(0)):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Meeting not found")
    return {
        "session_id": session.id,
        "messages": [m.to_dict() for m in session.chat if m.created_at > since],
        "transcript": [s.to_dict() for s in session.transcript[-20:]],
        "server_time": time.time(),
    }

@app.post("/api/meetings/{session_id}/chat")
async def chat(session_id: str, req: ChatRequest):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Meeting not found")
    msg = ChatMessage(id=new_id("msg"), role="user", text=req.text, engine="user")
    session.add_message(msg)
    answer = await orchestrator.answer(session, req.text)
    await broadcast_message(session, answer)
    return answer.to_dict(include_debug=req.include_debug)

@app.post("/api/meetings/{session_id}/recap")
async def recap(session_id: str):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Meeting not found")
    return await orchestrator.recap(session)

@app.post("/api/meetings/{session_id}/transcript")
async def inject_transcript(session_id: str, event: dict):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Meeting not found")
    from app.models import TranscriptSegment
    from datetime import datetime
    
    final = event.get("final", True)
    text = (event.get("text") or "").strip()
    speaker = event.get("speaker", "Speaker")
    t_start = event.get("t_start")
    t_end = event.get("t_end")
    
    if not text:
        return {"ok": False, "reason": "No text provided"}
    
    if t_start is None:
        t_start = session.clock() if hasattr(session, 'clock') else time.time()
    if t_end is None:
        t_end = t_start + max(1.0, len(text.split()) / 2.6)
    
    segment = TranscriptSegment(
        id=new_id("seg"),
        speaker=speaker,
        text=text,
        t_start=round(t_start, 2),
        t_end=round(t_end, 2),
        is_final=final,
        source="caption",
        asr_lag_ms=0,
    )
    
    added = session.add_segment(segment)
    if added:
        await session.emit({"type": "transcript.segment", "segment": segment.to_dict()})
        orchestrator.schedule_ambient(session)
        return {"ok": True, "segment": segment.to_dict()}
    return {"ok": True, "segment": None}

async def _start_audio(session: MeetingSession, source: str, mode: str, start_at_s: float = 0.0):
    if session.id in streamers:
        await stop_audio(session, stop_asr=True)
    session.stream_started_wall = time.time()
    session.audio.offset_s = max(0.0, start_at_s)
    asr = await ensure_asr(session, use_speaker_map=(mode == "sample"))
    streamer = AudioStreamer(session, asr.feed)
    streamers[session.id] = streamer
    await streamer.start(source, mode=mode, start_at_s=start_at_s)
    return session.audio.to_dict()

@app.post("/api/meetings/{session_id}/audio/url")
async def audio_url(session_id: str, req):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Meeting not found")
    url = req.url.strip() if hasattr(req, 'url') else str(req).strip()
    if not url.startswith(("http://", "https://")):
        local = resolve_local_audio(url)
        if local:
            state = await _start_audio(session, str(local), "sample", req.start_at_s if hasattr(req, 'start_at_s') else 0.0)
            return {"ok": True, "audio": state, "playback_url": "/demo/sample_meeting.mp3"}
        raise HTTPException(400, "Unrecognized URL or path")
    
    validate_remote_url(url)
    state = await _start_audio(session, url, "url", req.start_at_s if hasattr(req, 'start_at_s') else 0.0)
    return {"ok": True, "audio": state, "playback_url": url}

def _sample_duration_s() -> float:
    try:
        return float(json.loads(settings.sample_transcript.read_text()).get("duration_s", 0.0))
    except Exception:
        return 0.0

@app.post("/api/meetings/{session_id}/demo")
async def start_demo(session_id: str):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Meeting not found")
    sample = Path(settings.sample_audio)
    if not sample.exists():
        raise HTTPException(500, "Sample audio is missing; run scripts/make_sample_audio.py")
    state = await _start_audio(session, str(sample), "sample", 0.0)
    return {
        "ok": True,
        "audio": state,
        "playback_url": "/demo/sample_meeting.mp3",
        "duration_s": _sample_duration_s(),
        "note": "Server streams the audio at 1x through ffmpeg → streaming ASR; the browser plays the same file for the room.",
    }

@app.post("/api/meetings/{session_id}/audio/file")
async def audio_file(session_id: str, file: UploadFile = File(...)):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Meeting not found")
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename or "meeting.wav")
    dest = UPLOAD_DIR / f"{int(time.time())}-{name}"
    size = 0
    with dest.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 200 * 1024 * 1024:
                raise HTTPException(413, "Audio file is too large for the demo (max 200 MB)")
            fh.write(chunk)
    state = await _start_audio(session, str(dest), "upload", 0.0)
    return {"ok": True, "audio": state, "saved": dest.name}

@app.post("/api/meetings/{session_id}/audio/stop")
async def stop_audio_endpoint(session_id: str):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Meeting not found")
    await stop_audio(session, stop_asr=True)
    return {"ok": True, "audio": session.audio.to_dict()}

@app.get("/api/kb/status")
async def kb_status():
    return kb.status()

@app.post("/api/kb/search")
async def kb_search(req: KbSearchRequest):
    chunks = kb.search(req.query, k=req.k)
    return {"query": req.query, "results": [c.to_dict() for c in chunks]}

@app.post("/api/kb/ingest/url")
async def kb_ingest_url(req: KbIngestUrlRequest):
    validate_remote_url(req.url)
    name = req.name or Path(urlparse(req.url).path).name or "uploaded.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    dest = UPLOAD_DIR / f"{int(time.time())}-{name}"
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        async with client.stream("GET", req.url) as resp:
            resp.raise_for_status()
            size = 0
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(1024 * 256):
                    size += len(chunk)
                    if size > 30 * 1024 * 1024:
                        raise HTTPException(413, "PDF is larger than 30 MB")
                    fh.write(chunk)
    if not dest.read_bytes()[:5] == b"%PDF-":
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "The URL did not return a PDF")
    source = await asyncio.to_thread(kb.add_pdf, dest, name, True)
    return {"ok": True, "source": source.__dict__, "kb": kb.status()}

@app.post("/api/kb/ingest/file")
async def kb_ingest_file(file: UploadFile = File(...)):
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename or "uploaded.pdf")
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF uploads are supported")
    dest = UPLOAD_DIR / f"{int(time.time())}-{name}"
    size = 0
    with dest.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 30 * 1024 * 1024:
                raise HTTPException(413, "PDF is larger than 30 MB")
            fh.write(chunk)
    source = await asyncio.to_thread(kb.add_pdf, dest, name, True)
    return {"ok": True, "source": source.__dict__, "kb": kb.status()}

@app.websocket("/ws/meetings/{session_id}")
async def meeting_ws(ws: WebSocket, session_id: str):
    session = store.get(session_id)
    if not session:
        await ws.close(code=4404)
        return
    await ws.accept()
    session.clients.add(ws)
    await ws.send_json({
        "type": "hello",
        "state": session.public_state(),
        "config": runtime_info(),
    })
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "audio":
                raw = base64.b64decode(msg.get("pcm", ""))
                if not raw:
                    continue
                if len(raw) % 2:
                    raw = raw[:-1]
                pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                if session.stream_started_wall is None:
                    session.stream_started_wall = time.time()
                    session.audio.mode = "mic"
                    session.audio.source = "microphone"
                    session.audio.status = "streaming"
                    session.audio.started_at = time.time()
                    session.audio.offset_s = 0.0
                    await session.emit({"type": "status", "audio": session.audio.to_dict()})
                asr = await ensure_asr(session, use_speaker_map=False)
                await asr.feed(pcm)
                session.audio.samples += len(pcm)
            elif kind == "chat":
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                await add_user_message(session, text)
                request_id = new_id("req")
                await session.emit({
                    "type": "chat.start",
                    "request_id": request_id,
                    "question": text,
                    "engine": llm.info.engine,
                })
                try:
                    async for event_kind, payload in orchestrator.stream_answer(session, text):
                        if event_kind == "delta":
                            await session.emit({
                                "type": "chat.delta",
                                "request_id": request_id,
                                "delta": payload,
                            })
                        elif event_kind == "done":
                            message = payload
                            await session.emit({
                                "type": "chat.done",
                                "request_id": request_id,
                                "message": message.to_dict(include_debug=True),
                            })
                except Exception as exc:
                    await session.emit({
                        "type": "chat.error",
                        "request_id": request_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
            elif kind == "control":
                action = msg.get("action")
                if action == "ambient":
                    session.ambient = bool(msg.get("enabled"))
                    await session.emit({"type": "ambient", "enabled": session.ambient})
                elif action == "start_mic":
                    await stop_audio(session, stop_asr=True)
                    session.stream_started_wall = None
                    session.audio = AudioState(
                        mode="mic", source="microphone", status="streaming", started_at=time.time()
                    )
                    await session.emit({"type": "status", "audio": session.audio.to_dict()})
                elif action == "stop_audio":
                    await stop_audio(session, stop_asr=True)
                elif action == "ping":
                    await ws.send_json({"type": "pong", "t": time.time()})
            elif kind == "ping":
                await ws.send_json({"type": "pong", "t": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass
    finally:
        session.clients.discard(ws)
