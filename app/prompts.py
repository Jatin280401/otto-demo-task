from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Iterable, Optional

from .config import PROMPTS_DIR, settings
from .models import ChatMessage, Evidence, KbChunk, MeetingInfo, PromptBundle, TranscriptSegment, fmt_ts

SYSTEM_PROMPT = PROMPTS_DIR / "otto_meeting_system.md"
ANSWER_TEMPLATE = PROMPTS_DIR / "otto_answer_user.md"
AMBIENT_PROMPT = PROMPTS_DIR / "otto_ambient_cue.md"
RECAP_PROMPT = PROMPTS_DIR / "otto_meeting_recap.md"

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()

def _sanitize_untrusted(text: str, max_chars: int = 2400) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"</?(transcript|knowledge_base|chat|user_message|doc)[^>]*>", "", text, flags=re.I)
    text = html.escape(text, quote=False)
    if len(text) > max_chars:
        text = text[:max_chars] + " …[truncated]"
    return text

def _query_tokens(text: str) -> set[str]:
    stop = {"the", "and", "for", "that", "this", "with", "what", "when", "who", "how", "does",
            "did", "was", "were", "are", "you", "our", "about", "from", "have", "has", "can"}
    return {t for t in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", text.lower()) if t not in stop}

def select_transcript(
    segments: list[TranscriptSegment],
    question: str,
    now_s: float,
    window_s: Optional[float] = None,
    max_chars: Optional[int] = None,
) -> list[TranscriptSegment]:
    if not segments:
        return []
    window_s = window_s or settings.transcript_window_s
    max_chars = max_chars or settings.transcript_max_chars
    cutoff = max(0.0, now_s - window_s)
    recent = [s for s in segments if s.t_end >= cutoff]
    earlier = [s for s in segments if s.t_end < cutoff]

    q = _query_tokens(question)
    scored = []
    for seg in earlier:
        text = seg.text.lower()
        overlap = sum(1 for t in q if t in text)
        if overlap:
            scored.append((overlap, seg.t_start, seg))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    matched = [s for _, _, s in scored[:3]]
    matched_ids = {s.id for s in matched}

    chosen = sorted([s for s in segments if s in recent or s.id in matched_ids], key=lambda s: s.t_start)
    out: list[TranscriptSegment] = []
    total = 0
    for seg in reversed(chosen):
        line_len = len(seg.text) + 24
        if total + line_len > max_chars:
            break
        out.append(seg)
        total += line_len
    out.reverse()
    return out

def format_transcript(segments: Iterable[TranscriptSegment], partial: Optional[str] = None) -> str:
    segs = list(segments)
    lines = []
    for seg in segs:
        speaker = _sanitize_untrusted(seg.speaker, 60) or "Speaker"
        text = _sanitize_untrusted(seg.text, 1200)
        suffix = " (partial)" if not seg.is_final else ""
        lines.append(f"[{fmt_ts(seg.t_start)}] {speaker}: {text}{suffix}")
    if partial:
        t = segs[-1].t_end if segs else 0
        lines.append(f"[{fmt_ts(t)}] Speaker: {_sanitize_untrusted(partial, 600)} (partial, may change)")
    if not lines:
        return "(no speech transcribed yet)"
    return "\n".join(lines)

def format_kb(chunks: list[KbChunk], max_total_chars: int = 6000) -> str:
    if not chunks:
        return "(no knowledge-base passages retrieved for this question)"
    parts = []
    used = 0
    for i, chunk in enumerate(chunks, start=1):
        section = _sanitize_untrusted(chunk.section or "Document", 120)
        body = _sanitize_untrusted(chunk.text, min(settings.kb_max_chars_per_chunk, 1400))
        block = (
            f'<doc id="K{i}" source="{_sanitize_untrusted(chunk.source, 120)}" '
            f'page="{chunk.page}" section="{section}" score="{chunk.score:.3f}">\n'
            f"{body}\n</doc>"
        )
        if used + len(block) > max_total_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)

def key_evidence(question: str, evidence: Evidence, transcript_limit: int = 3,
                 kb_limit: int = 3) -> str:
    from .kb import kb  

    q = _query_tokens(question)
    scored: list[tuple[int, float, TranscriptSegment]] = []
    for seg in evidence.transcript:
        text = seg.text.lower()
        overlap = sum(1 for t in q if t in text)
        scored.append((overlap, seg.t_start, seg))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    transcript_picks = [s for _, _, s in scored[:transcript_limit]]
    if evidence.transcript and evidence.transcript[-1] not in transcript_picks:
        transcript_picks.append(evidence.transcript[-1])
    transcript_picks.sort(key=lambda s: s.t_start)

    lines: list[str] = []
    for seg in transcript_picks:
        lines.append(
            f'- [meeting {fmt_ts(seg.t_start)}] {_sanitize_untrusted(seg.speaker, 40)}: '
            f'"{_sanitize_untrusted(seg.text, 320)}"'
        )
    if evidence.kb:
        for sent, chunk in kb.best_sentences(question, evidence.kb, limit=kb_limit):
            section = _sanitize_untrusted(chunk.section or "Document", 70)
            lines.append(f'- [KB p.{chunk.page} · {section}] "{_sanitize_untrusted(sent, 320)}"')
    if not lines:
        return "(no evidence matched this question)"
    return "\n".join(lines)

def format_chat(messages: list[ChatMessage], limit: int = 6) -> str:
    if not messages:
        return "(no prior chat)"
    lines = []
    for msg in messages[-limit:]:
        who = "User" if msg.role == "user" else "Otto"
        text = _sanitize_untrusted(msg.text, 360)
        lines.append(f"{who}: {text}")
    return "\n".join(lines)

def language_hint(question: str) -> str:
    if re.search(r"[\u0900-\u097F]", question):
        return "Hindi (Devanagari) detected; answer in Hindi."
    hinglish = {"kya", "kaise", "kaun", "hai", "haan", "nahi", "batao", "kar", "karo", "kyun", "mein"}
    if len(hinglish & set(re.findall(r"[a-z]+", question.lower()))) >= 2:
        return "Hinglish detected; answer in natural Hinglish."
    return "Match the language of the user message."

def build_answer_prompt(
    meeting: MeetingInfo,
    question: str,
    evidence: Evidence,
    recent_chat: list[ChatMessage],
    partial: Optional[str] = None,
    now_s: float = 0.0,
    engine_model: str = "grounded",
    temperature: float = 0.2,
    max_tokens: int = 700,
) -> PromptBundle:
    system = _read(SYSTEM_PROMPT)
    template = _read(ANSWER_TEMPLATE)
    source_names = sorted({c.source for c in evidence.kb})
    sources = ", ".join(source_names) if source_names else "EarnOut handover PDF"
    rendered = template
    replacements = {
        "{{meeting_title}}": _sanitize_untrusted(meeting.title, 200),
        "{{meeting_id}}": meeting.id,
        "{{meeting_clock}}": fmt_ts(now_s),
        "{{transcript_window_s}}": str(int(settings.transcript_window_s)),
        "{{kb_sources}}": sources,
        "{{language_hint}}": language_hint(question),
        "{{transcript}}": format_transcript(evidence.transcript, partial),
        "{{kb_chunks}}": format_kb(evidence.kb),
        "{{key_evidence}}": key_evidence(question, evidence),
        "{{recent_chat}}": format_chat(recent_chat),
        "{{question}}": _sanitize_untrusted(question, 1600),
    }
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    return PromptBundle(
        system=system,
        user=rendered,
        model=engine_model,
        temperature=temperature,
        max_tokens=max_tokens,
        version=settings.prompt_version,
        context_chars=len(system) + len(rendered),
    )

def build_ambient_prompt(meeting: MeetingInfo, transcript: list[TranscriptSegment],
                         recent_chat: list[ChatMessage], kb_chunks: list[KbChunk],
                         now_s: float) -> PromptBundle:
    system = _read(AMBIENT_PROMPT)
    user = (
        f"## MEETING\n{_sanitize_untrusted(meeting.title, 160)} · position {fmt_ts(now_s)}\n\n"
        f"## RECENT TRANSCRIPT\n<transcript>\n{format_transcript(transcript)}\n</transcript>\n\n"
        f"## RETRIEVED KNOWLEDGE\n<knowledge_base>\n{format_kb(kb_chunks, 5000)}\n</knowledge_base>\n\n"
        f"## RECENT CHAT\n<chat>\n{format_chat(recent_chat, 4)}\n</chat>\n\n"
        "Decide now. Return only the JSON object."
    )
    return PromptBundle(system=system, user=user, model="ambient", temperature=0.0,
                        max_tokens=300, version=settings.prompt_version,
                        context_chars=len(system) + len(user))

def build_recap_prompt(meeting: MeetingInfo, transcript: list[TranscriptSegment],
                       kb_chunks: list[KbChunk]) -> PromptBundle:
    system = _read(RECAP_PROMPT)
    user = (
        f"## MEETING\n{_sanitize_untrusted(meeting.title, 160)} · duration {fmt_ts(transcript[-1].t_end if transcript else 0)}\n\n"
        f"## TRANSCRIPT\n<transcript>\n{format_transcript(transcript)}\n</transcript>\n\n"
        f"## RELEVANT KNOWLEDGE\n<knowledge_base>\n{format_kb(kb_chunks, 5000)}\n</knowledge_base>\n\n"
        "Return only the recap JSON."
    )
    return PromptBundle(system=system, user=user, model="recap", temperature=0.0,
                        max_tokens=900, version=settings.prompt_version,
                        context_chars=len(system) + len(user))
