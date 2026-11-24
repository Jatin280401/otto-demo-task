from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import numpy as np

from .config import settings
from .meeting import MeetingSession

FeedFn = Callable[[np.ndarray], Awaitable[None]]

SAMPLE_RATE = 16000
BYTES_PER_CHUNK = 3200  

class AudioStreamer:

    def __init__(self, session: MeetingSession, feed: FeedFn):
        self.session = session
        self.feed = feed
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.task: Optional[asyncio.Task] = None
        self._stop = False

    def is_running(self) -> bool:
        return bool(self.task and not self.task.done())

    async def start(self, source: str, mode: str = "url", start_at_s: float = 0.0) -> None:
        if self.is_running():
            await self.stop()
        self._stop = False
        self.session.audio.mode = mode
        self.session.audio.source = source
        self.session.audio.status = "starting"
        self.session.audio.started_at = time.time()
        self.session.audio.offset_s = max(0.0, start_at_s)
        self.session.audio.samples = 0
        self.session.audio.error = None
        self.session.stream_started_wall = time.time()
        self.task = asyncio.create_task(self._run(source, start_at_s), name=f"audio-{self.session.id}")
        await self.session.emit({"type": "status", "audio": self.session.audio.to_dict()})

    async def _run(self, source: str, start_at_s: float) -> None:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-re",
        ]
        if start_at_s > 0:
            cmd += ["-ss", f"{start_at_s:.2f}"]
        cmd += [
            "-i", source,
            "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
        ]
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.session.audio.status = "streaming"
            await self.session.emit({"type": "status", "audio": self.session.audio.to_dict()})
            last_status = time.time()
            while not self._stop:
                assert self.proc.stdout is not None
                data = await self.proc.stdout.read(BYTES_PER_CHUNK)
                if not data:
                    break
                if len(data) % 2:
                    data = data[:-1]
                pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                self.session.audio.samples += len(pcm)
                await self.feed(pcm)
                now = time.time()
                if now - last_status >= 1.0:
                    last_status = now
                    await self.session.emit({
                        "type": "status",
                        "audio": self.session.audio.to_dict(),
                        "asr": {"partial": bool(self.session.partial)},
                    })
            if self.proc.returncode is None:
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=2)
                except Exception:
                    self.proc.terminate()
            err = b""
            if self.proc.stderr is not None:
                try:
                    err = await asyncio.wait_for(self.proc.stderr.read(1200), timeout=1)
                except Exception:
                    err = b""
            final_clock = self.session.audio.clock
            self.session.audio.offset_s = round(final_clock, 2)
            self.session.audio.ended_at = time.time()
            if self._stop:
                self.session.audio.status = "stopped"
            elif self.proc.returncode not in (0, None) and err:
                self.session.audio.status = "error"
                self.session.audio.error = err.decode("utf-8", "replace")[-400:]
            else:
                self.session.audio.status = "ended"
        except asyncio.CancelledError:
            self.session.audio.status = "stopped"
        except Exception as exc:
            self.session.audio.status = "error"
            self.session.audio.error = f"{type(exc).__name__}: {exc}"
        finally:
            if self.proc and self.proc.returncode is None:
                self.proc.terminate()
            await self.session.emit({"type": "status", "audio": self.session.audio.to_dict()})

    async def stop(self) -> None:
        self._stop = True
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        if self.task and not self.task.done():
            try:
                await asyncio.wait_for(self.task, timeout=4)
            except Exception:
                self.task.cancel()
        self.session.audio.status = "stopped"
        self.session.audio.ended_at = time.time()
        self.session.audio.offset_s = round(self.session.audio.clock, 2)
        await self.session.emit({"type": "status", "audio": self.session.audio.to_dict()})

def resolve_local_audio(name: str) -> Optional[Path]:
    candidates = [
        Path(name),
        Path(settings.sample_audio).parent / name,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None
