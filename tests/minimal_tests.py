import json
import pathlib

import pytest

from app.config import settings
from app.kb import kb, KnowledgeBase
from app.models import (
    ChatMessage,
    Evidence,
    KbChunk,
    MeetingInfo,
    TranscriptSegment,
    Timings,
    fmt_ts,
    new_id,
)
from app.llm import LLMClient, EngineInfo
from app.main import app
from fastapi.testclient import TestClient

class TestConfig:
    def test_settings_have_reasonable_defaults(self):
        assert settings.transcript_window_s == 90.0
        assert settings.kb_top_k == 5
        assert settings.temperature == 0.15
        assert settings.partial_interval_ms == 900

    def test_prompt_version_is_set(self):
        assert settings.prompt_version == "otto-meeting-v1.0"

class TestModels:
    def test_new_id(self):
        id1 = new_id("test")
        id2 = new_id("test")
        assert id1 != id2

    def test_meeting_info(self):
        mi = MeetingInfo(id="m1", title="Test meeting", meeting_code=None, source="demo")
        d = mi.model_dump()
        assert d["id"] == "m1"
        assert d["title"] == "Test meeting"

class TestKB:
    @pytest.fixture(autouse=True)
    def load_kb(self):
        kb.load_defaults()
        yield

    def test_status_has_chunks_and_pages(self):
        st = kb.status()
        assert st["chunks"] > 0
        assert st["pages"] > 0
        assert "default_pdf" in st

    def test_search_returns_chunks(self):
        results = kb.search("who owns Otto?", k=3)
        assert len(results) > 0
        for c in results:
            assert hasattr(c, "page")
            assert hasattr(c, "section")
            assert hasattr(c, "score")
            assert hasattr(c, "text")

    def test_best_sentences_returns_definitional_sentences(self):
        cs = kb.search("who owns Otto?", k=10)
        best = kb.best_sentences("who owns Otto?", cs, limit=3)
        assert len(best) > 0
        for sent, chunk in best:
            assert len(sent) >= 38  
            assert chunk.page is not None

class TestPrompts:
    def test_answer_template_contains_contract(self):
        t = pathlib.Path("prompts/otto_answer_user.md").read_text(encoding="utf-8")
        assert "USER MESSAGE" in t
        assert "RESPONSE CONTRACT" in t

    def test_system_prompt_contains_key_rules(self):
        s = pathlib.Path("prompts/otto_meeting_system.md").read_text(encoding="utf-8")
        assert "EVIDENCE SOURCES AND HOW TO TREAT THEM" in s
        assert "Every factual sentence you write must be traceable" in s

class TestLLM:
    @pytest.fixture(autouse=True)
    def detect_llm(self):
        c = LLMClient()
        result = c.detect()
        assert result.available or result.engine == "grounded"
        yield

    def test_detect_returns_engine_info(self):
        c = LLMClient()
        info = c.detect()
        assert info.engine in ("ollama", "openai", "grounded")
        assert info.model or info.detail

class TestAPI:
    @pytest.fixture(autouse=True)
    def server(self):
        self.client = TestClient(app)
        yield

    def test_health(self):
        r = self.client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_config_returns_llm_and_asr_and_kb(self):
        r = self.client.get("/api/config")
        assert r.status_code == 200
        d = r.json()
        assert d["llm"]["engine"] in ("ollama", "openai", "grounded")
        assert d["asr"]["loaded"] or d["asr"]["error"] is None
        assert d["kb"]["chunks"] > 0

    def test_create_meeting(self):
        r = self.client.post("/api/meetings", json={"title": "Test meeting"})
        assert r.status_code == 200
        sid = r.json()["id"]
        assert sid.startswith("mtg_")

    def test_create_meeting_with_code(self):
        r = self.client.post("/api/meetings", json={"title": "Coded meet", "meeting_code": "abc-defg-hij"})
        assert r.status_code == 200
        assert r.json()["meeting_code"] == "abc-defg-hij"

    def test_ingest_known_pdf(self):
        r = self.client.get("/api/kb/status")
        assert r.status_code == 200
        d = r.json()
        assert d["chunks"] > 0

    def test_kb_search_endpoint(self):
        r = self.client.post("/api/kb/search", json={"query": "who owns Otto?", "k": 3})
        assert r.status_code == 200
        d = r.json()
        assert "query" in d
        assert len(d["results"]) > 0
        for c in d["results"]:
            assert "page" in c
            assert "score" in c

    def test_chat_end_to_end(self):
        m = self.client.post("/api/meetings", json={"title": "E2E test"}).json()
        sid = m["id"]

        self.client.post(f"/api/meetings/{sid}/transcript", json={
            "speaker": "Sarah", "text": "I think Hammadur owns most of Otto.", "t_start": 4, "t_end": 6, "final": True
        })
        self.client.post(f"/api/meetings/{sid}/transcript", json={
            "speaker": "Daniel", "text": "Yes. Chapter eight says nearly all of the Otto work is Hammadur Rahman's.", "t_start": 12, "t_end": 14, "final": True
        })

        r = self.client.post(f"/api/meetings/{sid}/chat", json={"text": "who owns Otto?"})
        assert r.status_code == 200
        d = r.json()
        assert d["engine"] in ("ollama", "grounded → ollama")
        assert "[KB p." in d["text"] or "[meeting" in d["text"]

    def test_recap_endpoint(self):
        m = self.client.post("/api/meetings", json={"title": "Recap test"}).json()
        sid = m["id"]
        r = self.client.post(f"/api/meetings/{sid}/recap")
        assert r.status_code == 200
        d = r.json()
        assert "engine" in d
        assert "transcript_segments" in d