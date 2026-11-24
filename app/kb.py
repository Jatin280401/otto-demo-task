from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .config import KNOWLEDGE_DIR, settings
from .models import KbChunk

HEADER_RE = re.compile(r"^EarnOut Backend .*Project Handover Manual \(PDF\)$", re.I)
PAGE_RE = re.compile(r"^page \d+$", re.I)
CHAPTER_RE = re.compile(r"^Chapter\s+(\d+[A-E]?)\s+(.+)$", re.I)
WS_RE = re.compile(r"\s+")

QUERY_EXPANSIONS: dict[str, str] = {
    "owner": "hammadur rahman deepak owns otto work",
    "owns": "hammadur rahman deepak otto ownership",
    "built": "hammadur rahman deepak implemented",
    "model": "gpt-5.4 temperature zero llm client",
    "temperature": "gpt-5.4 temperature voice pass",
    "knowledge directory": "upload docx pdf pptx xlsx tags indexed documents",
    "documents": "knowledge directory upload tags indexed document library",
    "upload": "docx pdf pptx xlsx twenty five megabytes tags",
    "meeting tasks": "otto meeting tasks transcript calendar assignee books task",
    "calendar": "otto books tasks calendar assignee approves slider",
    "transcript": "otto meeting tasks only new part transcript slicing",
    "live": "meeting assistant live audio latency",
    "latency": "streaming first token transcript slicing latency",
    "google meet": "meetings tactical meeting routes deepak google",
    "knowledge": "knowledge directory documents indexed otto answers",
    "prompt": "prompt catalogue otto meeting tasks system human",
    "voice": "voice pass temperature 0.7 natural replies",
}

def _norm(text: str) -> str:
    return WS_RE.sub(" ", text).strip()

def _stem(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("ches", "shes", "xes", "ses")) and len(token) > 5:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token

def _tokens(text: str) -> set[str]:
    return {
        _stem(t)
        for t in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", text.lower())
        if t not in STOP
    }

STOP = {
    "the", "and", "for", "that", "this", "with", "from", "what", "when", "where", "which",
    "who", "how", "does", "did", "are", "was", "were", "will", "would", "could", "should",
    "have", "has", "had", "into", "about", "there", "their", "them", "they", "you", "your",
    "our", "its", "not", "but", "can", "just", "now", "then", "than", "also", "any", "all",
}

def _split_units(text: str) -> list[str]:
    units: list[str] = []
    for raw_line in text.splitlines():
        line = _norm(raw_line)
        if not line or HEADER_RE.match(line) or PAGE_RE.match(line):
            continue
        if len(line) <= 340:
            units.append(line)
            continue
        for part in re.split(r"(?<=[.!?])\s+", line):
            part = _norm(part)
            if part:
                units.append(part)
    return units

def _pack_chunks(units: list[str], max_chars: int, overlap: int = 180) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for unit in units:
        if current and length + len(unit) + 1 > max_chars:
            chunk = "\n".join(current).strip()
            if chunk:
                chunks.append(chunk)
            tail: list[str] = []
            tail_len = 0
            for prev in reversed(current):
                if tail_len + len(prev) > overlap:
                    break
                tail.insert(0, prev)
                tail_len += len(prev) + 1
            current = tail
            length = tail_len
        current.append(unit)
        length += len(unit) + 1
    if current:
        chunk = "\n".join(current).strip()
        if chunk:
            chunks.append(chunk)
    return chunks

def page_heading(reader_page, fallback: str) -> str:
    spans: list[tuple[float, str]] = []

    def visit(text, cm, tm, font_dict, font_size):
        value = _norm(text or "")
        if not value or PAGE_RE.match(value) or HEADER_RE.match(value):
            return
        try:
            base_font = str((font_dict or {}).get("/BaseFont", ""))
        except Exception:
            base_font = ""
        if font_size >= 9.6 and "Bold" in base_font:
            spans.append((float(font_size), value))

    try:
        reader_page.extract_text(visitor_text=visit)
    except Exception:
        return fallback
    if not spans:
        return fallback
    spans.sort(key=lambda s: (-s[0], len(s[1])))
    heading = spans[0][1]
    heading = re.sub(r"\s+(Production|Testing|Unused)$", "", heading)
    return heading[:160] or fallback

@dataclass
class KbSource:
    name: str
    pages: int
    chunks: int
    path: str
    added_at: float

class KnowledgeBase:

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.chunks: list[KbChunk] = []
        self.sources: list[KbSource] = []
        self._vectorizer: Optional[TfidfVectorizer] = None
        self._matrix = None
        self._built_at: Optional[float] = None
        self._chunk_texts: list[str] = []
        self._page_count = 0

    def load_defaults(self) -> None:
        pdf = Path(settings.kb_pdf)
        if pdf.exists():
            self.add_pdf(pdf, source_name=pdf.name, persist=False)
        for extra in [p.strip() for p in settings.kb_extra_pdfs.split(",") if p.strip()]:
            path = Path(extra).expanduser()
            if path.exists():
                self.add_pdf(path, source_name=path.name, persist=False)
        self._load_sources_file()

    def _sources_file(self) -> Path:
        return KNOWLEDGE_DIR / "sources.json"

    def _load_sources_file(self) -> None:
        path = self._sources_file()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except Exception:
            return
        default = str(Path(settings.kb_pdf).name)
        for entry in data.get("uploads", []):
            if entry.get("name") == default:
                continue
            p = Path(entry.get("path", ""))
            if p.exists():
                self.add_pdf(p, source_name=entry.get("name", p.name), persist=False)

    def _save_sources_file(self) -> None:
        payload = {
            "uploads": [
                {"name": s.name, "path": s.path, "pages": s.pages, "chunks": s.chunks,
                 "added_at": s.added_at}
                for s in self.sources
                if s.name != Path(settings.kb_pdf).name
            ]
        }
        try:
            self._sources_file().write_text(json.dumps(payload, indent=2))
        except Exception:
            pass

    def add_pdf(self, path: Path, source_name: Optional[str] = None, persist: bool = True) -> KbSource:
        path = Path(path)
        source_name = source_name or path.name
        reader = PdfReader(str(path))
        pages = len(reader.pages)
        current_chapter = "Front matter"
        new_chunks: list[KbChunk] = []

        for page_index, page in enumerate(reader.pages):
            try:
                raw = page.extract_text() or ""
            except Exception:
                raw = ""
            text = "\n".join(
                line for line in raw.splitlines()
                if line.strip()
            )
            if len(_norm(text)) < 60:
                continue

            chapter_match = CHAPTER_RE.search(_norm(text.splitlines()[0] if text.splitlines() else ""))
            chapter_on_page = None
            for line in text.splitlines():
                m = CHAPTER_RE.match(_norm(line))
                if m:
                    chapter_on_page = f"Chapter {m.group(1)} · {m.group(2).strip()}"
                    current_chapter = chapter_on_page
                    break
            heading = page_heading(page, fallback=chapter_on_page or current_chapter)
            section = heading if heading else current_chapter

            units = _split_units(text)
            for i, chunk_text in enumerate(_pack_chunks(units, settings.kb_max_chars_per_chunk)):
                digest = hashlib.sha1(chunk_text.encode("utf-8")).hexdigest()[:10]
                new_chunks.append(KbChunk(
                    id=f"kb-{hashlib.sha1(source_name.encode()).hexdigest()[:5]}-p{page_index + 1}-{i}-{digest}",
                    text=chunk_text,
                    page=page_index + 1,
                    section=section,
                    source=source_name,
                ))

        with self.lock:
            existing = {c.id for c in self.chunks}
            added = [c for c in new_chunks if c.id not in existing]
            self.chunks.extend(added)
            self._page_count += pages
            self.sources = [s for s in self.sources if s.name != source_name]
            self.sources.append(KbSource(
                name=source_name,
                pages=pages,
                chunks=len(added),
                path=str(path),
                added_at=time.time(),
            ))
            self._rebuild()
            if persist:
                self._save_sources_file()
        return self.sources[-1]

    def _rebuild(self) -> None:
        if not self.chunks:
            self._vectorizer = None
            self._matrix = None
            self._chunk_texts = []
            self._built_at = time.time()
            return
        self._chunk_texts = [
            f"{c.source} page {c.page} {c.section}\n{c.text}" for c in self.chunks
        ]
        self._vectorizer = TfidfVectorizer(
            stop_words="english",
            ngram_range=(1, 2),
            sublinear_tf=True,
            max_df=0.9,
            min_df=1,
            max_features=180_000,
        )
        self._matrix = self._vectorizer.fit_transform(self._chunk_texts)
        self._built_at = time.time()

    def _expanded_query(self, query: str) -> str:
        q = query.lower()
        extra: list[str] = []
        for key, expansion in QUERY_EXPANSIONS.items():
            if key in q:
                extra.append(expansion)
        for token in re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", query):
            extra.append(token.lower())
        return query + (" " + " ".join(extra) if extra else "")

    def search(self, query: str, k: int = 5) -> list[KbChunk]:
        with self.lock:
            if not self.chunks or self._vectorizer is None or self._matrix is None:
                return []
            expanded = self._expanded_query(query)
            q_vec = self._vectorizer.transform([expanded])
            sims = cosine_similarity(q_vec, self._matrix).ravel()
            q_terms = _tokens(query)
            order = sims.argsort()[::-1]

            scored: list[tuple[float, int]] = []
            for idx in order[: max(40, k * 8)]:
                base = float(sims[idx])
                if base <= 0.0:
                    continue
                chunk = self.chunks[int(idx)]
                text_l = chunk.text.lower()
                overlap = sum(1 for t in q_terms if t in text_l)
                boost = min(0.12, overlap * 0.018)
                if "otto" in query.lower() and "otto" in text_l:
                    boost += 0.05
                if any(t in chunk.section.lower() for t in q_terms):
                    boost += 0.02
                scored.append((base + boost, int(idx)))

            scored.sort(reverse=True)
            picked: list[KbChunk] = []
            per_page: dict[int, int] = {}
            for score, idx in scored:
                chunk = self.chunks[idx]
                if per_page.get(chunk.page, 0) >= 2:
                    continue
                copy = KbChunk(
                    id=chunk.id, text=chunk.text, page=chunk.page,
                    section=chunk.section, source=chunk.source, score=score,
                )
                picked.append(copy)
                per_page[chunk.page] = per_page.get(chunk.page, 0) + 1
                if len(picked) >= k:
                    break
            return picked

    def best_sentences(self, query: str, chunks: list[KbChunk], limit: int = 3) -> list[tuple[str, KbChunk]]:
        q = _tokens(self._expanded_query(query))
        candidates: list[tuple[float, str, KbChunk]] = []
        for chunk in chunks:
            text = chunk.text.replace(" | ", ". ")
            for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
                sent = _norm(sent)
                if len(sent) < 38 or len(sent) > 480 or len(sent.split()) < 5:
                    continue
                if re.match(r"^(page \d+|EarnOut Backend|Chapter \d)", sent, re.I):
                    continue
                if sent.endswith(("Changed", "Summary", "Overview", "Features", "Prompts", "Guide")) and len(sent.split()) < 8:
                    continue
                s_tokens = _tokens(sent)
                overlap = len(q & s_tokens) if q else 0
                bonus = 0.0
                low = sent.lower()
                for marker in (" is ", " are ", " uses ", " owns ", " added ", " can ", " must ", " only "):
                    if marker in low:
                        bonus += 0.12
                if any(w in low for w in ("hammadur", "deepak", "otto", "knowledge directory")):
                    bonus += 0.1
                score = overlap + bonus + min(chunk.score, 0.4) * 0.5
                candidates.append((score, sent, chunk))
        candidates.sort(key=lambda x: (-x[0], len(x[1])))
        out: list[tuple[str, KbChunk]] = []
        seen: set[str] = set()
        for _, sent, chunk in candidates:
            key = sent[:120].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((sent, chunk))
            if len(out) >= limit:
                break
        return out

    def status(self) -> dict:
        with self.lock:
            return {
                "chunks": len(self.chunks),
                "pages": self._page_count,
                "sources": [
                    {"name": s.name, "pages": s.pages, "chunks": s.chunks, "path": s.path}
                    for s in self.sources
                ],
                "built_at": self._built_at,
                "vocabulary": len(self._vectorizer.vocabulary_) if self._vectorizer else 0,
                "default_pdf": str(settings.kb_pdf),
            }

kb = KnowledgeBase()
