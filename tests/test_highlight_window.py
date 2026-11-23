"""Highlight director window comes from the export preset; Groq/cloud privacy flag."""
import pytest

import agents_crew
import presets


def test_highlight_window_comes_from_preset():
    assert presets.highlight_window(None) == (8.0, 16.0)
    for pid, p in presets.PRESETS.items():
        lo, hi = presets.highlight_window(pid)
        assert 8 <= lo < hi <= p["max_duration"], pid
    assert presets.highlight_window("tiktok") != presets.highlight_window(None)
    with pytest.raises(ValueError):
        presets.highlight_window("nope")


def test_director_prompt_uses_window_not_hardcoded_8_16():
    lo, hi = presets.highlight_window("youtube_shorts")
    p = agents_crew.director_system_prompt(lo, hi)
    assert f"{lo:g} to {hi:g} seconds" in p and "8 to 16" not in p
    assert "8 to 16 seconds" in agents_crew.director_system_prompt(*presets.highlight_window(None))


def test_clamp_highlight():
    # too long -> cut to the max
    assert agents_crew.clamp_highlight(10, 100, 300, 15, 60) == (10, 70)
    # too short -> extended to the min
    assert agents_crew.clamp_highlight(10, 14, 300, 15, 60) == (10, 25)
    # extension would pass the end of the video -> start moves earlier
    assert agents_crew.clamp_highlight(95, 98, 100, 15, 60) == (85, 100)
    # already fine / out of range start
    assert agents_crew.clamp_highlight(20, 50, 300, 15, 60) == (20, 50)
    s, e = agents_crew.clamp_highlight(-5, 4, 300, 8, 16)
    assert s == 0 and 8 <= e - s <= 16


def test_crew_signature_accepts_preset():
    import inspect
    assert "export_preset" in inspect.signature(agents_crew.run_autonomous_agent_crew).parameters
    from server import AgentCrewRequest
    assert AgentCrewRequest(project_id="abc", export_preset="tiktok").export_preset == "tiktok"
    with pytest.raises(ValueError):
        AgentCrewRequest(project_id="abc", export_preset="vimeo")


def test_mode_exposes_cloud_text_flag(client, monkeypatch):
    import llm
    import config
    monkeypatch.setattr(config, "GROQ_API_KEY", "", raising=False)
    assert client.get("/api/mode").json()["sends_text_to_cloud"] is False
    monkeypatch.setattr(llm.cfg, "GROQ_API_KEY", "gsk_test_fake_key_for_unit_test")
    monkeypatch.setattr(llm.cfg, "LLM_PROVIDER", "auto")
    m = client.get("/api/mode").json()
    assert m["mode"] == "cloud" and m["sends_text_to_cloud"] is True
    monkeypatch.setattr(llm.cfg, "LLM_PROVIDER", "none")
    assert client.get("/api/mode").json()["sends_text_to_cloud"] is False
