"""LLM access for CLIP MASTER: Groq (cloud) or local Ollama ("no-key mode").

Mode selection (``LLM_PROVIDER`` in ``.env``):
  auto   -> Groq when ``GROQ_API_KEY`` is set, otherwise local Ollama (default)
  groq   -> always Groq (fails clearly if the key is missing)
  ollama -> always local Ollama
  none   -> no LLM at all; heuristics only

Transcription never needs a key: it always runs locally.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

import config as cfg

LANGUAGES = {"fr": "French", "en": "English", "es": "Spanish", "de": "German", "pt": "Portuguese", "it": "Italian", "ar": "Arabic"}

_PROBE_TTL = 15.0
_probe_cache: dict[str, Any] = {"t": 0.0, "ok": False, "models": []}


def provider() -> str:
    """Effective provider: groq | ollama | none."""
    choice = cfg.LLM_PROVIDER
    if choice == "none":
        return "none"
    if choice == "groq":
        return "groq"
    if choice == "ollama":
        return "ollama"
    return "groq" if cfg.GROQ_API_KEY else "ollama"


def _ollama_root() -> str:
    url = cfg.OLLAMA_URL.rstrip("/")
    url = re.sub(r"/api/(chat|tags|generate)$", "", url)
    return url


def ollama_chat_url() -> str:
    return _ollama_root() + "/api/chat"


def probe_ollama(force: bool = False) -> tuple[bool, list[str]]:
    """Is a local Ollama server reachable? Cached for a few seconds. Returns (ok, model names)."""
    now = time.time()
    if not force and now - _probe_cache["t"] < _PROBE_TTL:
        return _probe_cache["ok"], list(_probe_cache["models"])
    ok, models = False, []
    try:
        with urllib.request.urlopen(_ollama_root() + "/api/tags", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = [m.get("name", "") for m in data.get("models", [])]
        ok = True
    except Exception:
        pass
    _probe_cache.update(t=now, ok=ok, models=models)
    return ok, models


def llm_status(probe: bool = True) -> dict[str, Any]:
    """Description of the active mode, for the UI and /api/mode."""
    prov = provider()
    info: dict[str, Any] = {
        "provider": prov,
        "mode": {"groq": "cloud", "ollama": "local", "none": "off"}[prov],
        "key_present": bool(cfg.GROQ_API_KEY),
        "transcription": "local",
        "model": "",
        "ready": False,
        "hint": "",
    }
    if prov == "groq":
        info["model"] = cfg.GROQ_MODEL_SMALL
        info["ready"] = bool(cfg.GROQ_API_KEY)
        if not info["ready"]:
            info["hint"] = "LLM_PROVIDER=groq but GROQ_API_KEY is empty"
    elif prov == "ollama":
        info["model"] = cfg.OLLAMA_MODEL
        # probe=False reuses the last probe result (used by the 650 ms agents poll: no network call)
        ok, models = probe_ollama() if probe else (_probe_cache["ok"], list(_probe_cache["models"]))
        base = cfg.OLLAMA_MODEL.split(":")[0]
        has_model = any(m == cfg.OLLAMA_MODEL or m.split(":")[0] == base for m in models)
        info["ready"] = ok and has_model
        info["ollama_reachable"] = ok
        if probe and not ok:
            info["hint"] = f"Ollama not reachable at {_ollama_root()} (install from ollama.com and start it)"
        elif probe and not has_model:
            info["hint"] = f"Run: ollama pull {cfg.OLLAMA_MODEL}"
    else:
        info["hint"] = "LLM disabled (LLM_PROVIDER=none): heuristics only"
    return info


def _strip_to_json(content: str) -> dict[str, Any]:
    content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.M).strip()
    m = re.search(r"\{[\s\S]*\}", content)
    if not m:
        raise ValueError(f"LLM did not return JSON: {content[:200]}")
    return json.loads(m.group(0))


def call_llm_json(system_prompt: str, user_prompt: str, use_big: bool = False, timeout: int | None = None) -> dict[str, Any]:
    """Call the active LLM (Groq or Ollama) and parse its JSON answer. Raises on any failure."""
    timeout = timeout or cfg.LLM_TIMEOUT
    prov = provider()
    if prov == "none":
        raise RuntimeError("LLM disabled (LLM_PROVIDER=none)")
    if prov == "groq":
        if not cfg.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is empty")
        payload = {
            "model": cfg.GROQ_MODEL_BIG if use_big else cfg.GROQ_MODEL_SMALL,
            "messages": [
                {"role": "system", "content": system_prompt + "\nRespond ONLY with valid JSON, no markdown fences."},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 2048,
        }
        req = urllib.request.Request(
            cfg.GROQ_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {cfg.GROQ_API_KEY}",
                "Content-Type": "application/json",
                "User-Agent": "ClipMaster/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = raw["choices"][0]["message"]["content"].strip()
    else:
        payload = {
            "model": cfg.OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt + "\nRespond ONLY with valid JSON."},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }
        req = urllib.request.Request(
            ollama_chat_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama unreachable at {_ollama_root()}: {exc.reason}") from exc
        content = (raw.get("message", {}).get("content") or "").strip()
    return _strip_to_json(content)


def translate_captions(captions: list[dict[str, Any]], target_lang: str, batch: int = 20) -> list[dict[str, Any]]:
    """Translate caption texts with the active LLM. Timings are kept; word timings are dropped.

    Returns new caption dicts (originals untouched, the source text is kept in ``text_src``).
    Raises if the LLM fails, so the caller can report it instead of silently shipping untranslated text.
    """
    lang = (target_lang or "").lower()[:2]
    if lang not in LANGUAGES:
        raise ValueError(f"Unsupported language: {target_lang!r} (supported: {', '.join(LANGUAGES)})")
    out = [dict(c) for c in captions]
    name = LANGUAGES[lang]
    system = (
        f"You translate subtitles into {name}. Keep each subtitle short, natural and in UPPERCASE. "
        "Keep slang and emotion; never add or merge lines. Return JSON: "
        '{"translations":[{"idx":0,"text":"..."}]} with exactly one entry per input line.'
    )
    for start in range(0, len(out), batch):
        chunk = out[start:start + batch]
        user = json.dumps({"lines": [{"idx": start + i, "text": c.get("text", "")} for i, c in enumerate(chunk)]}, ensure_ascii=False)
        res = call_llm_json(system, user, use_big=False)
        got = {int(t["idx"]): str(t.get("text", "")).strip() for t in res.get("translations", []) if isinstance(t, dict) and "idx" in t}
        for i, c in enumerate(chunk):
            new = got.get(start + i)
            if new:
                c.setdefault("text_src", c.get("text", ""))
                c["text"] = new.upper()[:60]
                c.pop("words", None)
        if not got:
            raise ValueError("LLM returned no translations")
    return out
