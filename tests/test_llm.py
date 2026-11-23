"""No-key mode: Groq -> local Ollama fallback, tested with mocked HTTP."""
import io
import json

import pytest

import config
import llm


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(calls, ollama_models=("qwen2.5:7b",), reply=None):
    def fake(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        calls.append(url)
        if url.endswith("/api/tags"):
            return _Resp(json.dumps({"models": [{"name": m} for m in ollama_models]}).encode())
        body = json.loads(req.data.decode())
        content = json.dumps(reply if reply is not None else {"ok": True, "model": body["model"]})
        if "groq" in url:
            return _Resp(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
        return _Resp(json.dumps({"message": {"content": content}}).encode())
    return fake


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    llm._probe_cache.update(t=0.0, ok=False, models=[])


def test_no_key_selects_ollama():
    assert llm.provider() == "ollama"
    assert llm.llm_status(probe=False)["mode"] == "local"


def test_key_selects_groq(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    assert llm.provider() == "groq"
    assert llm.llm_status()["mode"] == "cloud"


def test_forced_none(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "none")
    assert llm.llm_status()["mode"] == "off"
    with pytest.raises(RuntimeError):
        llm.call_llm_json("s", "u")


def test_ollama_call_uses_local_url(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen(calls))
    out = llm.call_llm_json("sys", "usr")
    assert out["model"] == config.OLLAMA_MODEL
    assert calls == ["http://localhost:11434/api/chat"]


def test_groq_call_used_when_key(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    calls = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen(calls))
    assert llm.call_llm_json("s", "u", use_big=True)["model"] == config.GROQ_MODEL_BIG
    assert "groq" in calls[0]


def test_ollama_url_forms(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_URL", "http://h:1/api/chat")
    assert llm.ollama_chat_url() == "http://h:1/api/chat"
    monkeypatch.setattr(config, "OLLAMA_URL", "http://h:1/")
    assert llm.ollama_chat_url() == "http://h:1/api/chat"


def test_status_ollama_states(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen([]))
    assert llm.llm_status()["ready"] is True
    llm._probe_cache["t"] = 0
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen([], ollama_models=("llama3:8b",)))
    st = llm.llm_status()
    assert not st["ready"] and "ollama pull" in st["hint"]

    def boom(*a, **k):
        raise llm.urllib.error.URLError("refused")
    llm._probe_cache["t"] = 0
    monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
    st = llm.llm_status()
    assert not st["ready"] and st["ollama_reachable"] is False
    with pytest.raises(RuntimeError, match="Ollama unreachable"):
        llm.call_llm_json("s", "u")


def test_json_extraction_strips_think_and_fences(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda r, timeout=None: _Resp(
        json.dumps({"message": {"content": "<think>x</think>```json\n{\"a\": 1}\n```"}}).encode()))
    assert llm.call_llm_json("s", "u") == {"a": 1}


def test_translate_captions(monkeypatch):
    caps = [{"id": "c1", "text": "HELLO", "start": 0, "end": 1, "words": [{"w": "HELLO"}]},
            {"id": "c2", "text": "WORLD", "start": 1, "end": 2}]
    reply = {"translations": [{"idx": 0, "text": "bonjour"}, {"idx": 1, "text": "monde"}]}
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen([], reply=reply))
    out = llm.translate_captions(caps, "fr")
    assert [c["text"] for c in out] == ["BONJOUR", "MONDE"]
    assert out[0]["text_src"] == "HELLO" and "words" not in out[0]
    assert out[0]["start"] == 0 and caps[0]["text"] == "HELLO"  # original untouched


def test_translate_rejects_bad_lang_and_empty(monkeypatch):
    with pytest.raises(ValueError):
        llm.translate_captions([{"text": "x"}], "xx")
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen([], reply={"translations": []}))
    with pytest.raises(ValueError):
        llm.translate_captions([{"text": "x"}], "en")


def test_mode_endpoint(client, monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen([]))
    d = client.get("/api/mode").json()
    assert d["mode"] == "local" and d["transcription"].startswith("faster-whisper")
