from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import ChatMessage, MeetingInfo, TranscriptSegment, new_id

@dataclass
class AudioState:
    mode: str = "idle"  
    source: str = ""
    status: str = "idle"
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    offset_s: float = 0.0
    samples: int = 0
    sample_rate: int = 16000
    error: Optional[str] = None

    @property
    def clock(self) -> float:
        if self.started_at is None:
            return self.offset_s
        if self.status in {"playing", "streaming"}:
            return self.offset_s + (time.time() - self.started_at)
        return self.offset_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "source": self.source,
            "status": self.status,
            "clock_s": round(self.clock, 1),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error,
            "samples": self.samples,
        }

@dataclass
class MeetingSession:
    id: str
    title: str
    meeting_code: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    source: str = "demo"
    transcript: list[TranscriptSegment] = field(default_factory=list)
    partial: str = ""
    partial_start: float = 0.0
    chat: list[ChatMessage] = field(default_factory=list)
    ambient: bool = True
    last_ambient_at: float = 0.0
    last_final_at: float = 0.0
    audio: AudioState = field(default_factory=AudioState)
    clients: set[Any] = field(default_factory=set)
    answer_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    asr_task: Any = None
    audio_task: Any = None
    stream_started_wall: Optional[float] = None
    asr_lag_ms: Optional[int] = None

    def clock(self) -> float:
        if self.audio.status in {"playing", "streaming"} and self.audio.started_at:
            return self.audio.offset_s + (time.time() - self.audio.started_at)
        if self.transcript:
            return self.transcript[-1].t_end
        return self.audio.clock

    def info(self) -> MeetingInfo:
        return MeetingInfo(
            id=self.id,
            title=self.title,
            meeting_code=self.meeting_code,
            created_at=self.created_at,
            source=self.source,
            ambient=self.ambient,
            audio=self.audio.to_dict(),
            transcript_count=len(self.transcript),
            chat_count=len(self.chat),
        )

    def add_segment(self, seg: TranscriptSegment) -> bool:
        text_key = re.sub(r"[^a-z0-9 ]", "", seg.text.lower()).strip()
        if not text_key:
            return False
        for prev in reversed(self.transcript[-6:]):
            prev_key = re.sub(r"[^a-z0-9 ]", "", prev.text.lower()).strip()
            if prev_key == text_key and abs(prev.t_end - seg.t_end) < 2.0:
                return False
            if prev_key and (prev_key in text_key or text_key in prev_key) and len(text_key) > 24:
                if abs(prev.t_end - seg.t_end) < 4.0:
                    return False
        if seg.t_start is None:
            seg.t_start = self.clock()
        if seg.t_end is None or seg.t_end <= seg.t_start:
            seg.t_end = seg.t_start + max(0.8, len(seg.text.split()) / 2.6)
        self.transcript.append(seg)
        self.transcript.sort(key=lambda s: s.t_start)
        self.partial = ""
        self.last_final_at = time.time()
        if seg.asr_lag_ms is not None:
            self.asr_lag_ms = seg.asr_lag_ms
        return True

    def set_partial(self, text: str, start_s: float) -> None:
        self.partial = text
        self.partial_start = start_s

    def add_message(self, message: ChatMessage) -> ChatMessage:
        self.chat.append(message)
        return message

    async def emit(self, payload: dict[str, Any]) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def public_state(self) -> dict[str, Any]:
        return {
            "meeting": self.info().model_dump(),
            "transcript": [s.to_dict() for s in self.transcript[-120:]],
            "partial": self.partial,
            "partial_start": self.partial_start,
            "chat": [m.to_dict() for m in self.chat[-40:]],
        }

class MeetingStore:
    def __init__(self) -> None:
        self.sessions: dict[str, MeetingSession] = {}

    def create(self, title: str, meeting_code: Optional[str] = None, source: str = "demo") -> MeetingSession:
        session = MeetingSession(id=new_id("mtg"), title=title, meeting_code=meeting_code, source=source)
        self.sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Optional[MeetingSession]:
        return self.sessions.get(session_id)

    def find_by_code(self, code: str) -> Optional[MeetingSession]:
        code = code.strip().lower()
        for session in self.sessions.values():
            if (session.meeting_code or "").lower() == code:
                return session
        return None

    def ensure_for_code(self, code: str, title: Optional[str] = None) -> MeetingSession:
        existing = self.find_by_code(code)
        if existing:
            return existing
        return self.create(title or f"Google Meet · {code}", meeting_code=code, source="google-meet")

store = MeetingStore()
