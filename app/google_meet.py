from __future__ import annotations

import re
from typing import Optional

import httpx

from .config import settings
from .models import CaptionEvent

MEET_RE = re.compile(r"meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})", re.I)
CODE_RE = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$", re.I)

def meet_code(value: str) -> Optional[str]:
    value = (value or "").strip()
    if CODE_RE.match(value):
        return value.lower()
    m = MEET_RE.search(value)
    return m.group(1).lower() if m else None

def caption_to_text(event: CaptionEvent) -> str:
    speaker = (event.speaker or "Speaker").strip()
    text = re.sub(r"\s+", " ", event.text or "").strip()
    return f"{speaker}: {text}" if text else ""

async def post_to_google_chat(text: str) -> dict:
    if not settings.google_chat_webhook_url:
        return {"posted": False, "reason": "GOOGLE_CHAT_WEBHOOK_URL is not configured"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(settings.google_chat_webhook_url, json={"text": text})
            resp.raise_for_status()
        return {"posted": True}
    except Exception as exc:
        return {"posted": False, "reason": f"{type(exc).__name__}: {exc}"}

def integration_status() -> dict:
    return {
        "mode": "caption-bridge + optional Google Chat webhook",
        "webhook_configured": bool(settings.google_chat_webhook_url),
        "audio_path": "Chrome extension captions OR tab-audio stream OR Meet Media API (preview)",
        "chat_path": "in-app overlay + optional Google Chat webhook",
        "notes": [
            "Google Meet has no public API to join a call or post into its in-call chat.",
            "The included MV3 extension reads live captions from meet.google.com and posts them to /api/meet/captions.",
            "For production audio, use the Google Meet Media API (Workspace developer preview) or a recording bot with consent.",
            "Meet add-ons can host the Otto side panel; a linked Google Chat space is the supported chat destination.",
        ],
    }
