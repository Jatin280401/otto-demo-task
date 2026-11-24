from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
KNOWLEDGE_DIR = ROOT / "knowledge"
PROMPTS_DIR = ROOT / "prompts"
STATIC_DIR = ROOT / "app" / "static"
UPLOAD_DIR = KNOWLEDGE_DIR / "uploads"

def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

@dataclass
class Settings:
    llm_engine: str = os.environ.get("OTTO_LLM", "auto")  
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openai_base_url: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.environ.get("OTTO_OPENAI_MODEL", "gpt-4o-mini")
    ollama_base_url: str = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.environ.get("OTTO_OLLAMA_MODEL", "qwen2.5:1.5b-instruct")
    llm_timeout_s: float = _float("OTTO_LLM_TIMEOUT_S", 60.0)
    max_output_tokens: int = _int("OTTO_MAX_OUTPUT_TOKENS", 300)
    temperature: float = _float("OTTO_TEMPERATURE", 0.15)

    asr_engine: str = os.environ.get("OTTO_ASR", "auto")  
    asr_model: str = os.environ.get("OTTO_ASR_MODEL", "base.en")
    asr_device: str = os.environ.get("OTTO_ASR_DEVICE", "auto")
    asr_compute_type: str = os.environ.get("OTTO_ASR_COMPUTE_TYPE", "int8")
    asr_language: str = os.environ.get("OTTO_ASR_LANGUAGE", "en")
    asr_initial_prompt: str = os.environ.get(
        "OTTO_ASR_PROMPT",
        "Otto, EarnOut, Hammadur Rahman, Deepak, LangGraph, Neo4j, OKR, XBRL, "
        "Google Meet, PDF, knowledge directory, transcript, ASR, latency, calendar.",
    )
    vad_threshold: float = _float("OTTO_VAD_THRESHOLD", 0.012)
    vad_silence_ms: int = _int("OTTO_VAD_SILENCE_MS", 650)
    vad_min_speech_ms: int = _int("OTTO_VAD_MIN_SPEECH_MS", 350)
    partial_interval_ms: int = _int("OTTO_PARTIAL_INTERVAL_MS", 900)

    transcript_window_s: float = _float("OTTO_TRANSCRIPT_WINDOW_S", 3600.0)
    transcript_max_chars: int = _int("OTTO_TRANSCRIPT_MAX_CHARS", 40000)
    kb_top_k: int = _int("OTTO_KB_TOP_K", 4)
    kb_max_chars_per_chunk: int = _int("OTTO_KB_CHUNK_CHARS", 700)
    context_max_chars: int = _int("OTTO_CONTEXT_MAX_CHARS", 60000)
    ambient_enabled: bool = _bool("OTTO_AMBIENT", True)
    ambient_cooldown_s: float = _float("OTTO_AMBIENT_COOLDOWN_S", 25.0)
    ambient_quiet_s: float = _float("OTTO_AMBIENT_QUIET_S", 3.0)

    kb_pdf: Path = field(default_factory=lambda: Path(os.environ.get(
        "OTTO_KB_PDF", str(KNOWLEDGE_DIR / "earnout-handover.pdf"))))
    kb_extra_pdfs: str = os.environ.get("OTTO_KB_EXTRA_PDFS", "")
    kb_allow_private_urls: bool = _bool("OTTO_ALLOW_PRIVATE_URLS", False)

    google_chat_webhook_url: str = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL", "")
    public_base_url: str = os.environ.get("OTTO_PUBLIC_URL", "http://localhost:8000")
    sample_audio: Path = field(default_factory=lambda: Path(os.environ.get(
        "OTTO_SAMPLE_AUDIO", str(DATA_DIR / "sample_meeting.wav"))))
    sample_transcript: Path = field(default_factory=lambda: Path(os.environ.get(
        "OTTO_SAMPLE_TRANSCRIPT", str(DATA_DIR / "sample_meeting.transcript.json"))))

    @property
    def prompt_version(self) -> str:
        return os.environ.get("OTTO_PROMPT_VERSION", "otto-meeting-v1.0")

settings = Settings()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
