from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx

from .config import settings
from .kb import kb
from .models import Evidence, KbChunk, MeetingInfo, PromptBundle, TranscriptSegment, fmt_ts

@dataclass
class EngineInfo:
    engine: str  
    model: str
    available: bool
    detail: str

class LLMClient:

    def __init__(self) -> None:
        self.info = EngineInfo("grounded", "grounded-composer", True, "No local or hosted LLM detected")
        self._client: Optional[httpx.AsyncClient] = None

    async def detect(self) -> EngineInfo:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout_s, connect=2.0))
        mode = (settings.llm_engine or "auto").lower()

        if mode in {"auto", "ollama"}:
            try:
                resp = await self._client.get(f"{settings.ollama_base_url}/api/tags", timeout=1.5)
                if resp.status_code == 200:
                    names = [m.get("name", "") for m in resp.json().get("models", [])]
                    if settings.ollama_model in names:
                        self.info = EngineInfo("ollama", settings.ollama_model, True,
                                               "Local Ollama model")
                        return self.info
                    stem = settings.ollama_model.split(":")[0]
                    matches = [n for n in names if n.startswith(stem)]
                    if matches:
                        self.info = EngineInfo("ollama", matches[0], True, "Local Ollama model (family match)")
                        return self.info
                    preferred = ("llama3.2", "llama3.1", "qwen", "mistral", "gemma", "phi")
                    for family in preferred:
                        found = [n for n in names if n.lower().startswith(family)]
                        if found:
                            self.info = EngineInfo("ollama", found[0], True,
                                                   "Local Ollama model (auto-selected)")
                            return self.info
                    if names:
                        self.info = EngineInfo("ollama", names[0], True,
                                               "Local Ollama model (first available)")
                        return self.info
            except Exception:
                pass
            if mode == "ollama":
                self.info = EngineInfo("grounded", "grounded-composer", True,
                                       "Ollama requested but not reachable; using grounded fallback")
                return self.info

        if settings.openai_api_key:
            self.info = EngineInfo("openai", settings.openai_model, True, "Hosted OpenAI-compatible API")
            return self.info

        self.info = EngineInfo("grounded", "grounded-composer", True,
                               "Grounded extractive composer (set OLLAMA_BASE_URL or OPENAI_API_KEY for generative answers)")
        return self.info

    def _messages(self, bundle: PromptBundle) -> list[dict]:
        return [
            {"role": "system", "content": bundle.system},
            {"role": "user", "content": bundle.user},
        ]

    async def stream(self, bundle: PromptBundle) -> AsyncIterator[str]:
        if self.info.engine == "ollama":
            async for chunk in self._stream_ollama(bundle):
                yield chunk
        elif self.info.engine == "openai":
            async for chunk in self._stream_openai(bundle):
                yield chunk
        else:
            raise RuntimeError("grounded engine does not stream through LLMClient")

    async def warmup(self) -> bool:
        if self.info.engine != "ollama" or self._client is None:
            return False
        try:
            resp = await self._client.post(
                f"{settings.ollama_base_url}/api/chat",
                json={
                    "model": self.info.model,
                    "messages": [{"role": "user", "content": "Say OK."}],
                    "stream": False,
                    "keep_alive": "30m",
                    "options": {"num_predict": 4, "temperature": 0},
                },
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def _stream_ollama(self, bundle: PromptBundle) -> AsyncIterator[str]:
        assert self._client is not None
        payload = {
            "model": self.info.model,
            "messages": self._messages(bundle),
            "stream": True,
            "keep_alive": "30m",
            "options": {
                "temperature": bundle.temperature,
                "num_predict": bundle.max_tokens,
                "num_ctx": 8192,
            },
        }
        async with self._client.stream("POST", f"{settings.ollama_base_url}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = data.get("message", {})
                piece = msg.get("content", "")
                if piece:
                    yield piece
                if data.get("done"):
                    break

    async def _stream_openai(self, bundle: PromptBundle) -> AsyncIterator[str]:
        assert self._client is not None
        payload = {
            "model": self.info.model,
            "messages": self._messages(bundle),
            "temperature": bundle.temperature,
            "max_tokens": bundle.max_tokens,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        async with self._client.stream(
            "POST", f"{settings.openai_base_url.rstrip('/')}/chat/completions",
            json=payload, headers=headers,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    data = json.loads(raw)
                    piece = data["choices"][0]["delta"].get("content") or ""
                except Exception:
                    continue
                if piece:
                    yield piece

    async def complete_json(self, bundle: PromptBundle) -> Optional[dict]:
        if self.info.engine == "grounded":
            return None
        assert self._client is not None
        try:
            if self.info.engine == "ollama":
                payload = {
                    "model": self.info.model,
                    "messages": self._messages(bundle),
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": bundle.temperature, "num_predict": bundle.max_tokens, "num_ctx": 8192},
                }
                resp = await self._client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
                resp.raise_for_status()
                content = resp.json().get("message", {}).get("content", "")
            else:
                payload = {
                    "model": self.info.model,
                    "messages": self._messages(bundle),
                    "temperature": bundle.temperature,
                    "max_tokens": bundle.max_tokens,
                    "response_format": {"type": "json_object"},
                }
                headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
                resp = await self._client.post(
                    f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                    json=payload, headers=headers,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
            return _parse_json(content)
        except Exception:
            return None

def _parse_json(content: str) -> Optional[dict]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else None
    except Exception:
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None

DECISION_RE = re.compile(
    r"\b(let'?s (decide|go with|use|keep)|we'?ll (keep|use|go|do|expose|add)|"
    r"agreed|decided|the plan is|we (are|'re) going to|final answer|let'?s do)\b",
    re.I,
)
ACTION_RE = re.compile(
    r"\b(i'?ll|i will|we'?ll|we will|can you|you should|we need to|let'?s|"
    r"follow up|draft|add|expose|make sure|send|schedule|book|review|confirm)\b",
    re.I,
)
QUESTION_RE = re.compile(r"\b(what|who|how|when|where|why|which|can you|could you|do we|did we|is there|are there)\b", re.I)

def _short_section(section: str) -> str:
    section = re.sub(r"\s+", " ", section or "").strip()
    section = section.split("—")[0].strip(" ·")
    return section[:32]

def _clean_sentence(s: str) -> str:
    s = s.replace("\uf0b7", "").replace("\x7f", "").replace("|", " · ")
    s = re.sub(r"\s+", " ", s).strip(" -–—")
    s = re.sub(r"\.\s*\.", ".", s)
    if not s.endswith((".", "!", "?")):
        s += "."
    return s

def _kb_cite(chunk: KbChunk) -> str:
    sec = _short_section(chunk.section)
    return f"[KB p.{chunk.page} · {sec}]" if sec else f"[KB p.{chunk.page}]"

def _meeting_cite(seg: TranscriptSegment) -> str:
    return f"[meeting {fmt_ts(seg.t_start)}]"

def _looks_like_placeholder(sentence: str) -> bool:
    low = sentence.lower()
    if '"budget"' in low or '"labour"' in low or '"infrastructure"' in low:
        return True
    if sentence.count('"') >= 4 or sentence.count("{") + sentence.count("}") >= 2:
        return True
    if low.startswith(("post /", "get /", "| ", "file ", "client ", "route ")):
        return True
    return False

def _transcript_matches(question: str, segments: list[TranscriptSegment], pattern: re.Pattern | None = None,
                        limit: int = 3) -> list[TranscriptSegment]:
    q = {t for t in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", question.lower())}
    scored = []
    for seg in segments:
        low = seg.text.lower()
        overlap = sum(1 for t in q if t in low)
        bonus = 2 if pattern and pattern.search(seg.text) else 0
        if overlap or bonus:
            scored.append((overlap + bonus, seg.t_end, seg))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [s for _, _, s in scored[:limit]]

def grounded_answer(question: str, meeting: MeetingInfo, evidence: Evidence,
                    transcript: list[TranscriptSegment], now_s: float) -> str:
    q = question.strip()
    low = q.lower()

    if re.search(r"(ignore (all )?(previous|prior|above) instructions|system prompt|reveal.*(prompt|instruction)|jailbreak|developer mode)", low):
        return ("I treat meeting audio and uploaded documents as data, so I won't follow instructions spoken "
                "in the transcript or reveal internal prompts. I can still answer questions from what was "
                "said and from the indexed project PDF.")

    asks_budget = any(w in low for w in ("budget", "cost", "price", "spend"))
    if asks_budget and not any(w in " ".join(s.text.lower() for s in transcript) for w in ("budget", "cost", "price", "spend")):
        return ("I don't see a budget in the recent transcript, and the retrieved pages only contain example "
                "budget fields inside prompt templates — not a real figure for this project. If it was discussed "
                "earlier, ask me to search the earlier transcript.")

    wants_decision = bool(re.search(r"\b(decide|decided|decision|agree|agreed|conclusion|settle)\b", low))
    wants_recap = bool(re.search(r"\b(recap|summar(y|ise|ize)|action items|next steps|takeaways)\b", low))
    wants_attribution = bool(re.search(r"\b(who (owns|said|mentioned|is responsible)|owner)\b", low))

    if wants_decision or wants_recap:
        decisions = _transcript_matches(q, transcript, DECISION_RE, limit=2)
        if decisions:
            quotes = "; ".join(f"“{_clean_sentence(s.text)}” {_meeting_cite(s)}" for s in decisions[:2])
            lead = "The decision recorded in this window" if wants_decision else "So far the meeting"
            extra = ""
            if wants_recap:
                actions = _transcript_matches(q, transcript, ACTION_RE, limit=3)
                action_lines = [s for s in actions if s.id not in {d.id for d in decisions}]
                if action_lines:
                    extra = " Follow-ups mentioned: " + "; ".join(
                        f"{s.speaker}: “{_clean_sentence(s.text)}” {_meeting_cite(s)}" for s in action_lines[:2]
                    )
            return f"{lead}: {quotes}.{extra}".replace("..", ".")

    kb_sentences = [
        (s, c) for s, c in kb.best_sentences(q, evidence.kb, limit=6)
        if not _looks_like_placeholder(s)
    ]
    q_terms = {t for t in re.findall(r"[a-z0-9][a-z0-9\-]{3,}", low)}
    kb_sentences.sort(
        key=lambda pair: (
            -sum(1 for t in q_terms if t in pair[0].lower()),
            len(pair[0]),
        )
    )
    transcript_hits = _transcript_matches(q, transcript, None, limit=2)

    if wants_attribution:
        owner = [p for p in kb_sentences if re.search(r"\b(owns?|owner|work is|added|wrote|built)\b", p[0], re.I)]
        if owner:
            kb_sentences = owner + [p for p in kb_sentences if p not in owner]

    if kb_sentences:
        sent, chunk = kb_sentences[0]
        answer = f"{_clean_sentence(sent)} {_kb_cite(chunk)}"
        if len(kb_sentences) > 1 and len(answer.split()) < 75:
            sent2, chunk2 = kb_sentences[1]
            if sent2[:60].lower() != sent[:60].lower():
                answer += f" {_clean_sentence(sent2)} {_kb_cite(chunk2)}"
        if transcript_hits:
            t = transcript_hits[0]
            answer += (f" In the meeting, {t.speaker} said “{_clean_sentence(t.text)}” "
                       f"{_meeting_cite(t)}.")
        return answer

    if transcript_hits:
        t = transcript_hits[0]
        answer = (f"In the meeting, {t.speaker} said “{_clean_sentence(t.text)}” {_meeting_cite(t)}.")
        if len(transcript_hits) > 1:
            t2 = transcript_hits[1]
            answer += f" {t2.speaker} added “{_clean_sentence(t2.text)}” {_meeting_cite(t2)}."
        return answer

    return (
        "I can't find that in the recent transcript or in the retrieved pages of the project PDF. "
        "If it was covered earlier in the meeting, ask me to search the earlier transcript, or upload the "
        "relevant document in the Knowledge tab."
    )

def grounded_ambient(question: str, meeting: MeetingInfo, evidence: Evidence,
                     transcript: list[TranscriptSegment], now_s: float) -> dict:
    if not transcript:
        return {"should_respond": False, "reason": "no_transcript", "confidence": 0.9,
                "message": "", "evidence_ids": []}
    recent = [s for s in transcript if s.t_end >= now_s - 14]
    if not recent:
        return {"should_respond": False, "reason": "quiet", "confidence": 0.9,
                "message": "", "evidence_ids": []}
    question_seg = None
    for seg in reversed(recent):
        if seg.text.strip().endswith("?") and QUESTION_RE.search(seg.text):
            question_seg = seg
            break
    if not question_seg:
        return {"should_respond": False, "reason": "no_question", "confidence": 0.8,
                "message": "", "evidence_ids": []}
    q = question_seg.text
    ev_kb = kb.search(q, k=3)
    ev = Evidence(transcript=[question_seg], kb=ev_kb)
    msg = grounded_answer(q, meeting, ev, transcript, now_s)
    if "can't find" in msg or "don't see" in msg:
        return {"should_respond": False, "reason": "no_evidence", "confidence": 0.8,
                "message": "", "evidence_ids": []}
    words = msg.split()
    if len(words) > 70:
        msg = " ".join(words[:70]).rstrip(".,") + "…"
    return {"should_respond": True, "reason": "unanswered_question", "confidence": 0.62,
            "message": msg, "evidence_ids": [question_seg.id] + [c.id for c in ev_kb[:2]]}

def grounded_recap(transcript: list[TranscriptSegment]) -> dict:
    decisions = []
    actions = []
    open_questions = []
    seen = set()
    for seg in transcript:
        text = _clean_sentence(seg.text)
        key = text[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        ts = fmt_ts(seg.t_start)
        if DECISION_RE.search(text):
            decisions.append({"text": text, "evidence": [ts]})
        if ACTION_RE.search(text) and not text.strip().endswith("?"):
            owner = seg.speaker if not re.search(r"\b(can you|could you|you should)\b", text, re.I) else "unconfirmed"
            actions.append({
                "title": text,
                "owner": owner,
                "due": None,
                "confidence": 0.55,
                "evidence": [ts],
            })
        if text.strip().endswith("?"):
            open_questions.append({"text": text, "evidence": [ts]})
    summary = "Live meeting with {} transcribed turns.".format(len(transcript))
    if decisions:
        summary = "The meeting covered " + " ".join(d["text"] for d in decisions[:2])
    return {
        "summary": summary[:400],
        "decisions": decisions[:6],
        "actions": actions[:8],
        "open_questions": open_questions[:4],
        "engine": "grounded-recap",
    }
