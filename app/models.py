from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

@dataclass
class TranscriptSegment:
    id: str
    speaker: str
    text: str
    t_start: float
    t_end: float
    is_final: bool = True
    source: str = "asr"  
    created_at: float = field(default_factory=time.time)
    asr_lag_ms: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def stamp(self) -> str:
        return fmt_ts(self.t_start)

def fmt_ts(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"

@dataclass
class KbChunk:
    id: str
    text: str
    page: int
    section: str
    source: str
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "page": self.page,
            "section": self.section,
            "source": self.source,
            "score": round(self.score, 4),
        }

    def citation(self) -> str:
        title = self.source.replace("-", " ").replace(".pdf", "")
        return f"KB p.{self.page} · {self.section or title}"

@dataclass
class Evidence:
    transcript: list[TranscriptSegment] = field(default_factory=list)
    kb: list[KbChunk] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "transcript": [s.to_dict() for s in self.transcript],
            "kb": [c.to_dict() for c in self.kb],
        }

@dataclass
class Timings:
    retrieval_ms: int = 0
    prompt_ms: int = 0
    first_token_ms: int = 0
    total_ms: int = 0
    asr_lag_ms: Optional[int] = None
    audio_chunk_ms: int = 100
    partial_interval_ms: int = 900

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class PromptBundle:
    system: str
    user: str
    model: str
    temperature: float
    max_tokens: int
    version: str
    context_chars: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def approx_tokens(self) -> int:
        return (len(self.system) + len(self.user)) // 4

@dataclass
class ChatMessage:
    id: str
    role: Literal["user", "assistant", "system"]
    text: str
    created_at: float = field(default_factory=time.time)
    mode: str = "reply"  
    engine: str = "grounded"
    citations: list[dict[str, Any]] = field(default_factory=list)
    evidence: Optional[Evidence] = None
    timings: Optional[Timings] = None
    prompt: Optional[PromptBundle] = None
    question: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self, include_debug: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "role": self.role,
            "text": self.text,
            "created_at": self.created_at,
            "mode": self.mode,
            "engine": self.engine,
            "citations": self.citations,
            "timings": self.timings.to_dict() if self.timings else None,
            "question": self.question,
            "error": self.error,
        }
        if include_debug:
            data["evidence"] = self.evidence.to_dict() if self.evidence else None
            data["prompt"] = self.prompt.to_dict() if self.prompt else None
        return data

class CreateMeetingRequest(BaseModel):
    title: str = "Otto live meeting"
    meeting_code: Optional[str] = None
    source: str = "demo"

class MeetingInfo(BaseModel):
    id: str
    title: str
    meeting_code: Optional[str] = None
    created_at: float
    source: str
    ambient: bool
    audio: dict[str, Any]
    transcript_count: int
    chat_count: int

class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    include_debug: bool = True

class AudioUrlRequest(BaseModel):
    url: str
    start_at_s: float = 0.0

class CaptionEvent(BaseModel):
    meeting_code: Optional[str] = None
    session_id: Optional[str] = None
    speaker: str = "Speaker"
    text: str = Field(min_length=1, max_length=2000)
    t_start: Optional[float] = None
    t_end: Optional[float] = None
    final: bool = True
    source: str = "caption"

class KbSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    k: int = 5

class KbIngestUrlRequest(BaseModel):
    url: str
    name: Optional[str] = None

class MeetJoinRequest(BaseModel):
    meet_url: str
    title: Optional[str] = None
