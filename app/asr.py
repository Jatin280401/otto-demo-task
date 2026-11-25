from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import numpy as np

from .config import settings
from .models import TranscriptSegment, fmt_ts, new_id

try:  
    from faster_whisper import WhisperModel  
    WHISPER_IMPORTED = True
except Exception:
    WhisperModel = None  
    WHISPER_IMPORTED = False

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1600  

EmitPartial = Callable[[str, float, float, Optional[int]], Awaitable[None]]
EmitFinal = Callable[[TranscriptSegment], Awaitable[None]]

def normalize_speaker(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        return "Speaker A"
    return "Speaker B" if re.search(r"\bB\b", name, re.I) or name.lower().endswith(" b") else "Speaker A"

class SpeakerMap:
    def __init__(self, path: Optional[Path] = None, enabled: bool = True):
        self.turns: list[tuple[float, float, str]] = []
        if not enabled:
            return
        try:
            data = json.loads((path or settings.sample_transcript).read_text())
            for s in data.get("segments", []):
                start = float(s.get("t_start", 0))
                end = float(s.get("t_end", start + 1))
                self.turns.append((start, end, normalize_speaker(s.get("speaker", "Speaker"))))
        except Exception:
            pass
        self.turns.sort()

    def speaker_at(self, t: float) -> str:
        for start, end, speaker in self.turns:
            if start - 0.35 <= t <= end + 0.35:
                return speaker
        if self.turns:
            return min(self.turns, key=lambda turn: abs(turn[0] - t))[2]
        return "Speaker A"

    def speaker_for_range(self, start: float, end: float) -> str:
        scores: dict[str, float] = {}
        for t_start, t_end, speaker in self.turns:
            overlap = max(0.0, min(end, t_end) - max(start, t_start))
            if overlap > 0:
                scores[speaker] = scores.get(speaker, 0.0) + overlap
        return max(scores, key=scores.get) if scores else "Speaker A"

_speaker_map: Optional[SpeakerMap] = None

def get_speaker_map() -> SpeakerMap:
    global _speaker_map
    if _speaker_map is None:
        _speaker_map = SpeakerMap()
    return _speaker_map

_whisper_model = None
_whisper_lock = asyncio.Lock()
_whisper_error: Optional[str] = None
_whisper_loaded = False

async def preload_whisper() -> None:
    global _whisper_model, _whisper_loaded, _whisper_error
    if not WHISPER_IMPORTED or settings.asr_engine == "off":
        return
    if _whisper_loaded:
        return
    async with _whisper_lock:
        if _whisper_loaded:
            return
        try:
            def load():
                return WhisperModel(
                    settings.asr_model,
                    device="cpu" if settings.asr_device == "auto" else settings.asr_device,
                    compute_type=settings.asr_compute_type,
                )
            _whisper_model = await asyncio.to_thread(load)
            _whisper_error = None
        except Exception as exc:
            _whisper_model = None
            _whisper_error = f"{type(exc).__name__}: {exc}"
        _whisper_loaded = True

def whisper_status() -> dict:
    return {
        "imported": WHISPER_IMPORTED,
        "loaded": _whisper_model is not None,
        "model": settings.asr_model,
        "error": _whisper_error,
        "engine": settings.asr_engine,
    }

def _rms(chunk: np.ndarray) -> float:
    if chunk.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))

class BaseASR:
    def __init__(self, session, emit_partial: EmitPartial, emit_final: EmitFinal):
        self.session = session
        self.emit_partial = emit_partial
        self.emit_final = emit_final
        self.queue: asyncio.Queue[Optional[np.ndarray]] = asyncio.Queue(maxsize=400)
        self.task: Optional[asyncio.Task] = None
        self.stopped = False

    async def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name=f"asr-{self.session.id}")

    async def feed(self, chunk: np.ndarray) -> None:
        if self.stopped:
            return
        try:
            self.queue.put_nowait(chunk.astype(np.float32, copy=False))
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(chunk.astype(np.float32, copy=False))
            except Exception:
                pass

    async def stop(self) -> None:
        self.stopped = True
        try:
            self.queue.put_nowait(None)
        except Exception:
            pass
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except Exception:
                self.task.cancel()

    async def run(self) -> None:
        raise NotImplementedError

class WhisperStreamingASR(BaseASR):

    def __init__(self, session, emit_partial: EmitPartial, emit_final: EmitFinal,
                 speaker_map: Optional[SpeakerMap] = None):
        super().__init__(session, emit_partial, emit_final)
        self.preroll: list[np.ndarray] = []
        self.utterance: list[np.ndarray] = []
        self.utterance_start = 0.0
        self.audio_cursor = 0.0
        self.speech_active = False
        self.silence_s = 0.0
        self.last_partial_s = 0.0
        self.noise_floor = 0.006
        self.partial_task: Optional[asyncio.Task] = None
        self.infer_lock = asyncio.Lock()
        self.speaker_map = speaker_map or get_speaker_map()
        self.utterance_speaker = "Speaker A"

    async def run(self) -> None:
        while True:
            chunk = await self.queue.get()
            if chunk is None:
                if self.speech_active and self.utterance:
                    await self._finalise()
                return
            await self._consume(chunk)

    async def _consume(self, chunk: np.ndarray) -> None:
        duration = len(chunk) / SAMPLE_RATE
        level = _rms(chunk)
        threshold = max(settings.vad_threshold, self.noise_floor * 2.6)

        if level < threshold:
            self.noise_floor = 0.97 * self.noise_floor + 0.03 * level
        is_speech = level >= threshold

        if not self.speech_active:
            self.preroll.append(chunk)
            if sum(len(c) for c in self.preroll) > SAMPLE_RATE * 0.4:
                self.preroll.pop(0)
            if is_speech:
                self.speech_active = True
                self.silence_s = 0.0
                self.utterance = list(self.preroll)
                self.utterance_start = max(0.0, self.audio_cursor - sum(len(c) for c in self.preroll) / SAMPLE_RATE)
                self.last_partial_s = self.audio_cursor
                self.preroll = []
                self.utterance_speaker = self.speaker_map.speaker_at(self.utterance_start)
        else:
            self.utterance.append(chunk)
            if is_speech:
                self.silence_s = 0.0
            else:
                self.silence_s += duration

            utt_duration = sum(len(c) for c in self.utterance) / SAMPLE_RATE
            if (self.audio_cursor - self.last_partial_s) * 1000 >= settings.partial_interval_ms:
                self.last_partial_s = self.audio_cursor
                if self.partial_task is None or self.partial_task.done():
                    self.partial_task = asyncio.create_task(self._partial())

            now_speaker = self.speaker_map.speaker_at(self.audio_cursor)
            if (now_speaker != self.utterance_speaker
                    and utt_duration * 1000 >= settings.vad_min_speech_ms
                    and self.audio_cursor - self.utterance_start > 1.0):
                await self._finalise()
            elif self.silence_s * 1000 >= settings.vad_silence_ms and utt_duration * 1000 >= settings.vad_min_speech_ms:
                await self._finalise()
            elif utt_duration >= 12.0:
                await self._finalise()

        self.audio_cursor += duration

    def _lag_ms(self, end_s: float) -> Optional[int]:
        if not self.session.stream_started_wall:
            return None
        elapsed = time.time() - self.session.stream_started_wall
        return max(0, int((elapsed - end_s) * 1000))

    async def _transcribe(self, audio: np.ndarray) -> str:
        if _whisper_model is None or audio.size == 0:
            return ""
        audio = np.ascontiguousarray(audio, dtype=np.float32)

        def run():
            segments, _info = _whisper_model.transcribe(
                audio,
                language=settings.asr_language or None,
                beam_size=1,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
                initial_prompt=settings.asr_initial_prompt,
            )
            return " ".join(s.text.strip() for s in segments).strip()

        async with self.infer_lock:
            try:
                return await asyncio.to_thread(run)
            except Exception:
                return ""

    async def _partial(self) -> None:
        if not self.speech_active or not self.utterance:
            return
        audio = np.concatenate(self.utterance[-int(8 * SAMPLE_RATE):])
        text = await self._transcribe(audio)
        if not text or not self.speech_active:
            return
        end_s = self.audio_cursor
        self.session.set_partial(text, self.utterance_start)
        await self.emit_partial(text, self.utterance_start, end_s, self._lag_ms(end_s))

    async def _finalise(self) -> None:
        if not self.utterance:
            self._reset()
            return
        if self.partial_task and not self.partial_task.done():
            try:
                await asyncio.wait_for(self.partial_task, timeout=2.0)
            except Exception:
                self.partial_task.cancel()
        audio = np.concatenate(self.utterance)
        text = await self._transcribe(audio)
        end_s = self.audio_cursor
        lag = self._lag_ms(end_s)
        if text and len(text.strip(" .,!?")) >= 2:
            segment = TranscriptSegment(
                id=new_id("seg"),
                speaker=self.speaker_map.speaker_for_range(self.utterance_start, end_s),
                text=text.strip(),
                t_start=round(self.utterance_start, 2),
                t_end=round(max(end_s, self.utterance_start + 0.5), 2),
                is_final=True,
                source="asr",
                asr_lag_ms=lag,
            )
            await self.emit_final(segment)
        self._reset()

    def _reset(self) -> None:
        self.utterance = []
        self.speech_active = False
        self.silence_s = 0.0
        self.session.partial = ""
        self.preroll = []
        self.utterance_speaker = "Speaker A"

class AlignmentASR(BaseASR):

    def __init__(self, session, emit_partial: EmitPartial, emit_final: EmitFinal,
                 transcript_path: Optional[Path] = None):
        super().__init__(session, emit_partial, emit_final)
        self.path = Path(transcript_path or settings.sample_transcript)
        self.offset = 0.0
        self.stop_event = asyncio.Event()

    async def feed(self, chunk: np.ndarray) -> None:
        return

    async def stop(self) -> None:
        self.stopped = True
        self.stop_event.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=3)
            except Exception:
                self.task.cancel()

    async def run(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            segments = data.get("segments", [])
        except Exception:
            segments = []
        started = self.session.stream_started_wall or time.time()
        for item in segments:
            if self.stopped:
                return
            target = float(item.get("t_start", 0)) + self.offset
            delay = target - (time.time() - started)
            if delay > 0:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
                    return  
                except asyncio.TimeoutError:
                    pass
            if self.stopped:
                return
            segment = TranscriptSegment(
                id=new_id("seg"),
                speaker=normalize_speaker(item.get("speaker", "Speaker")),
                text=item.get("text", ""),
                t_start=float(item.get("t_start", 0)),
                t_end=float(item.get("t_end", target + 2)),
                is_final=True,
                source="alignment",
                asr_lag_ms=0,
            )
            await self.emit_final(segment)

def build_asr(session, emit_partial: EmitPartial, emit_final: EmitFinal,
              use_speaker_map: bool = True) -> BaseASR:
    mode = (settings.asr_engine or "auto").lower()
    if mode == "off":
        return AlignmentASR(session, emit_partial, emit_final)
    if mode == "alignment":
        return AlignmentASR(session, emit_partial, emit_final)
    if WHISPER_IMPORTED and (mode == "whisper" or _whisper_model is not None):
        speaker_map = get_speaker_map() if use_speaker_map else SpeakerMap(enabled=False)
        return WhisperStreamingASR(session, emit_partial, emit_final, speaker_map=speaker_map)
    return AlignmentASR(session, emit_partial, emit_final)
