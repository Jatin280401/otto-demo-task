from __future__ import annotations

import asyncio
import re
import time
from typing import AsyncIterator, Optional

from .config import settings
from .kb import kb
from .llm import LLMClient, grounded_ambient, grounded_answer, grounded_recap
from .meeting import MeetingSession
from .models import ChatMessage, Evidence, KbChunk, Timings, TranscriptSegment, fmt_ts, new_id
from .prompts import (
    build_ambient_prompt,
    build_answer_prompt,
    build_recap_prompt,
    select_transcript,
)

KB_RE = re.compile(r"\[KB\s+p\.?\s*(\d+)[^\]]*\]", re.I)
MEETING_RE = re.compile(r"\[meeting\s+(\d{1,2}):(\d{2})\]", re.I)
LABEL_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:\*\*)?(answer|evidence|sources?|next step|recommendation|summary)(?:\*\*)?\s*:?\s*$",
    re.I,
)
LABEL_INLINE_RE = re.compile(
    r"(?:^|\n)\s*(?:#{1,4}\s*)?(?:\*\*)?(answer|evidence|sources?|next step|recommendation|summary)(?:\*\*)?\s*:\s*",
    re.I,
)

def clean_model_output(text: str, max_words: int = 180) -> str:
    text = text.replace("\r", "")
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if LABEL_RE.match(stripped):
            continue
        if re.fullmatch(r"(?:#{1,6}\s*)?\*\*[^*].*[^*]\*\*", stripped) or re.fullmatch(r"#{1,6}\s+\S.*", stripped):
            continue
        stripped = re.sub(r"^#{1,6}\s*", "", stripped)
        stripped = re.sub(r"^\*\*(.+?)\*\*\s*:\s*", r"\1: ", stripped)
        stripped = re.sub(r"^\*\*\s*", "", stripped)
        stripped = re.sub(r"\*\*\s*$", "", stripped).strip()
        lines.append(stripped)
    text = "\n".join(lines)
    text = LABEL_INLINE_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(
        r"(This (?:passage|information|answer) is (?:from|based on) the (?:retrieved )?(?:knowledge base|transcript)[^.]*\.)",
        "", text, flags=re.I,
    )
    text = re.sub(r"(I have not seen any conflicting information[^.]*\.)", "", text, flags=re.I)
    words = text.split()
    if len(words) > max_words:
        cut = " ".join(words[:max_words]).rstrip(" ,;:")
        last = max(cut.rfind("."), cut.rfind("?"), cut.rfind("!"))
        if last > len(cut) * 0.55:
            cut = cut[: last + 1]
        text = cut + " …"
    return text.strip()

class Orchestrator:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def prepare(self, session: MeetingSession, question: str, window_s: Optional[float] = None):
        t0 = time.perf_counter()
        now_s = session.clock()
        transcript_evidence = select_transcript(
            session.transcript, question, now_s, window_s=window_s
        )
        recent_text = " ".join(s.text for s in transcript_evidence[-4:])
        query = (question + " " + recent_text[-500:]).strip()
        kb_evidence = kb.search(query, k=settings.kb_top_k)
        retrieval_ms = int((time.perf_counter() - t0) * 1000)

        evidence = Evidence(transcript=transcript_evidence, kb=kb_evidence)
        t1 = time.perf_counter()
        prior_questions = [m for m in session.chat[-8:] if m.role == "user"]
        prompt = build_answer_prompt(
            meeting=session.info(),
            question=question,
            evidence=evidence,
            recent_chat=prior_questions,
            partial=session.partial,
            now_s=now_s,
            engine_model=self.llm.info.model,
            temperature=settings.temperature,
            max_tokens=settings.max_output_tokens,
        )
        prompt_ms = int((time.perf_counter() - t1) * 1000)
        timings = Timings(
            retrieval_ms=retrieval_ms,
            prompt_ms=prompt_ms,
            asr_lag_ms=session.asr_lag_ms,
            audio_chunk_ms=100,
            partial_interval_ms=settings.partial_interval_ms,
        )
        return evidence, prompt, timings, now_s

    async def stream_answer(self, session: MeetingSession, question: str,
                            mode: str = "reply") -> AsyncIterator[tuple[str, object]]:
        async with session.answer_lock:
            t_start = time.perf_counter()
            evidence, prompt, timings, now_s = self.prepare(session, question)
            parts: list[str] = []
            first_token_ms: Optional[int] = None
            engine = self.llm.info.engine
            error: Optional[str] = None

            if engine == "grounded":
                text = grounded_answer(question, session.info(), evidence,
                                       evidence.transcript, now_s)
                for word in re.findall(r"\S+\s*", text):
                    if first_token_ms is None:
                        first_token_ms = int((time.perf_counter() - t_start) * 1000)
                    parts.append(word)
                    yield "delta", word
                    await asyncio.sleep(0.012)
            else:
                try:
                    async for delta in self.llm.stream(prompt):
                        if not delta:
                            continue
                        if first_token_ms is None:
                            first_token_ms = int((time.perf_counter() - t_start) * 1000)
                        parts.append(delta)
                        yield "delta", delta
                except Exception as exc:
                    error = f"Local model failed ({type(exc).__name__}); grounded fallback used."
                    parts = []
                    text = grounded_answer(question, session.info(), evidence,
                                           evidence.transcript, now_s)
                    for word in re.findall(r"\S+\s*", text):
                        if first_token_ms is None:
                            first_token_ms = int((time.perf_counter() - t_start) * 1000)
                        parts.append(word)
                        yield "delta", word
                        await asyncio.sleep(0.008)

            full_text = "".join(parts).strip()
            if not full_text:
                full_text = grounded_answer(question, session.info(), evidence,
                                            evidence.transcript, now_s)
                yield "delta", full_text

            if engine != "grounded" and not error:
                cleaned = clean_model_output(full_text)
                if cleaned and cleaned != full_text:
                    full_text = cleaned
                if len(full_text.split()) < 4:
                    full_text = grounded_answer(question, session.info(), evidence,
                                                evidence.transcript, now_s)
                    error = "Local model returned no usable text; grounded composer used."
            full_text, citation_warning = self._ensure_citations(full_text, evidence)
            if citation_warning:
                error = (error + " " if error else "") + citation_warning
            citations = self._structured_citations(full_text, evidence)
            timings.first_token_ms = first_token_ms or 0
            timings.total_ms = int((time.perf_counter() - t_start) * 1000)

            message = ChatMessage(
                id=new_id("msg"),
                role="assistant",
                text=full_text,
                mode=mode,
                engine=engine if not error else f"{engine} → grounded",
                citations=citations,
                evidence=evidence,
                timings=timings,
                prompt=prompt,
                question=question,
                error=error,
            )
            session.add_message(message)
            yield "done", message

    async def answer(self, session: MeetingSession, question: str,
                     mode: str = "reply") -> ChatMessage:
        message: Optional[ChatMessage] = None
        async for kind, payload in self.stream_answer(session, question, mode=mode):
            if kind == "done":
                message = payload  
        assert message is not None
        return message

    def _ensure_citations(self, text: str, evidence: Evidence) -> tuple[str, Optional[str]]:
        known_pages = {c.page for c in evidence.kb}
        known_times = {int(round(s.t_start)) for s in evidence.transcript}
        unknown_pages = []
        for m in KB_RE.finditer(text):
            page = int(m.group(1))
            if page not in known_pages:
                unknown_pages.append(page)
        if unknown_pages:
            text = KB_RE.sub(lambda m: m.group(0) if int(m.group(1)) in known_pages else "", text)
            text = re.sub(r"\s{2,}", " ", text).strip()

        has_citation = bool(KB_RE.search(text) or MEETING_RE.search(text))
        warning = None
        if unknown_pages:
            warning = f"Removed unverified KB citation(s): p.{', p.'.join(map(str, unknown_pages))}."
        refusal = bool(re.search(
            r"\b(?:I (?:do not|don't) see|not (?:in|covered)|no (?:evidence|mention)|"
            r"cannot find|does not (?:cover|mention|include))\b", text, re.I,
        ))
        if not has_citation and not refusal and len(text.split()) >= 8 and (evidence.kb or evidence.transcript):
            refs = []
            if evidence.kb:
                c = evidence.kb[0]
                refs.append(f"[KB p.{c.page}]")
            if evidence.transcript:
                s = evidence.transcript[-1]
                refs.append(f"[meeting {fmt_ts(s.t_start)}]")
            if refs:
                text = text.rstrip() + "\n\nSources: " + " · ".join(refs)
        return text, warning

    def _structured_citations(self, text: str, evidence: Evidence) -> list[dict]:
        cited_pages = {int(m.group(1)) for m in KB_RE.finditer(text)}
        cited_times = set()
        for m in MEETING_RE.finditer(text):
            cited_times.add(int(m.group(1)) * 60 + int(m.group(2)))
        out: list[dict] = []
        for chunk in evidence.kb:
            out.append({
                "type": "kb",
                "page": chunk.page,
                "section": chunk.section,
                "source": chunk.source,
                "snippet": chunk.text[:420],
                "score": round(chunk.score, 3),
                "cited": chunk.page in cited_pages,
            })
        for seg in evidence.transcript:
            out.append({
                "type": "meeting",
                "t": round(seg.t_start, 1),
                "stamp": fmt_ts(seg.t_start),
                "speaker": seg.speaker,
                "text": seg.text,
                "cited": any(abs(int(round(seg.t_start)) - t) <= 2 for t in cited_times),
            })
        return out

    def schedule_ambient(self, session: MeetingSession) -> None:
        if not (session.ambient and settings.ambient_enabled):
            return
        if time.time() - session.last_ambient_at < settings.ambient_cooldown_s:
            return
        task = getattr(session, "_ambient_task", None)
        if task and not task.done():
            task.cancel()

        async def runner():
            await asyncio.sleep(settings.ambient_quiet_s)
            try:
                await self.ambient_tick(session)
            except Exception:
                pass

        session._ambient_task = asyncio.create_task(runner())  

    async def ambient_tick(self, session: MeetingSession) -> Optional[ChatMessage]:
        if not (session.ambient and settings.ambient_enabled):
            return None
        if time.time() - session.last_ambient_at < settings.ambient_cooldown_s:
            return None
        if not session.transcript:
            return None
        now_s = session.clock()
        recent = [s for s in session.transcript if s.t_end >= now_s - 22]
        if not recent:
            return None
        question_context = " ".join(s.text for s in recent[-4:])
        kb_evidence = kb.search(question_context + " meeting", k=3)
        evidence = Evidence(transcript=recent[-6:], kb=kb_evidence)

        result: Optional[dict] = None
        if self.llm.info.engine != "grounded":
            prompt = build_ambient_prompt(session.info(), recent[-8:], session.chat[-6:],
                                          kb_evidence, now_s)
            result = await self.llm.complete_json(prompt)
        if result is None:
            result = grounded_ambient(question_context, session.info(), evidence,
                                      recent, now_s)
        if not result or not result.get("should_respond"):
            return None
        message_text = (result.get("message") or "").strip()
        if not message_text:
            return None
        message_text, warning = self._ensure_citations(message_text, evidence)
        timings = Timings(retrieval_ms=0, prompt_ms=0, first_token_ms=0, total_ms=0,
                          asr_lag_ms=session.asr_lag_ms)
        msg = ChatMessage(
            id=new_id("msg"),
            role="assistant",
            text=message_text,
            mode="proactive",
            engine=self.llm.info.engine,
            citations=self._structured_citations(message_text, evidence),
            evidence=evidence,
            timings=timings,
            question=None,
            error=warning,
        )
        session.add_message(msg)
        session.last_ambient_at = time.time()
        await session.emit({"type": "chat.message", "message": msg.to_dict()})
        return msg

    async def recap(self, session: MeetingSession) -> dict:
        transcript = session.transcript
        if not transcript:
            return {"summary": "No speech has been transcribed yet.", "decisions": [],
                    "actions": [], "open_questions": [], "engine": "none"}
        kb_evidence = kb.search("meeting decisions action items Otto", k=3)
        result: Optional[dict] = None
        if self.llm.info.engine != "grounded":
            prompt = build_recap_prompt(session.info(), transcript, kb_evidence)
            result = await self.llm.complete_json(prompt)
        if result is None:
            result = grounded_recap(transcript)
        result.setdefault("decisions", [])
        result.setdefault("actions", [])
        result.setdefault("open_questions", [])
        result["transcript_segments"] = len(transcript)
        result["duration_s"] = round(transcript[-1].t_end, 1)
        result["engine"] = result.get("engine", self.llm.info.engine)
        return result
