"""Real FFmpeg runs on a tiny generated clip."""
import json
import subprocess

import pipeline
from tools_registry import get_ffmpeg_path


def probe(path):
    ffprobe = get_ffmpeg_path().replace("ffmpeg", "ffprobe")
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=width,height,r_frame_rate,codec_type:format=duration",
                              "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,r_frame_rate,codec_type:format=duration",
                              "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_local_import(project):
    assert project["info"]["width"] == 640 and 5.5 < project["info"]["duration"] < 6.5
    assert len(project["filmstrip"]) > 0 and "pull" in project["stage_completed"]
    assert (pipeline.PROJECTS_DIR / project["id"] / "source.mp4").exists()


def test_load_save_roundtrip(project):
    p = pipeline.load_project(project["id"])
    p["font_size"] = 50
    pipeline.save_project(project["id"], p)
    assert pipeline.load_project(project["id"])["font_size"] == 50


def test_project_id_validation():
    for bad in ["../x", "a/b", "", "proj_" + "a" * 100, "..\\x"]:
        try:
            pipeline.validate_project_id(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def _ship(project, **kw):
    p = pipeline.load_project(project["id"])
    p["captions"] = [{"id": "c1", "text": "HELLO WORLD", "start": 0.5, "end": 2.0, "color": "yellow"},
                     {"id": "c2", "text": "SECOND LINE", "start": 2.5, "end": 4.0, "color": "yellow"}]
    pipeline.save_project(project["id"], p)
    return pipeline.stage_05_ship(project["id"], layout_mode="CENTER CROP", normalize_audio=False,
                                  retention_bar=False, interpolate_slowmo=False, **kw)


def test_ship_is_1080x1920_30fps(project):
    res = _ship(project)
    info = probe(pipeline.OUTPUTS_DIR / res["export"]["filename"])
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"], v["r_frame_rate"]) == (1080, 1920, "30/1")
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert res["export"]["preset"] is None


def test_ship_preset_trims_and_records(project, monkeypatch):
    import presets
    monkeypatch.setitem(presets.PRESETS["tiktok"], "max_duration", 3)
    res = _ship(project, export_preset="tiktok", caption_style="cyan")
    dur = float(probe(pipeline.OUTPUTS_DIR / res["export"]["filename"])["format"]["duration"])
    assert 2.5 < dur < 3.6
    assert res["export"]["preset"] == "tiktok"
    assert pipeline.load_project(project["id"])["caption_style"] == "tiktok_white"  # cyan not allowed on TikTok


def test_ass_respects_safe_margin(tmp_path):
    out = tmp_path / "c.ass"
    pipeline._write_ass_subtitles([{"text": "HI", "start": 0, "end": 1}], out, font_size=40, split_mode=False,
                                  caption_style="white", min_margin_v=600)
    style = [l for l in out.read_text().splitlines() if l.startswith("Style:")][0]
    assert style.split(",")[21] == "600"


def test_ass_strips_override_braces(tmp_path):
    out = tmp_path / "c.ass"
    pipeline._write_ass_subtitles([{"text": "{\\an8}HI", "start": 0, "end": 1}], out, font_size=40)
    assert "{" not in out.read_text().split("[Events]")[1]


def test_atempo_chain():
    assert pipeline._atempo_chain(1.0) == "atempo=1.0000"
    assert pipeline._atempo_chain(0.25) == "atempo=0.5,atempo=0.5000"


def test_translate_stage(project, monkeypatch):
    import llm
    p = pipeline.load_project(project["id"])
    p["captions"] = [{"id": "c1", "text": "HELLO", "start": 0, "end": 1}]
    pipeline.save_project(project["id"], p)
    monkeypatch.setattr(llm, "call_llm_json", lambda s, u, **k: {"translations": [{"idx": 0, "text": "salut"}]})
    out = pipeline.stage_translate(project["id"], "fr")
    assert out["captions"][0]["text"] == "SALUT" and out["caption_lang"] == "fr"
    assert "SALUT" in (pipeline.PROJECTS_DIR / project["id"] / "captions.srt").read_text()
    # second translation starts again from the source text
    monkeypatch.setattr(llm, "call_llm_json", lambda s, u, **k: {"translations": [{"idx": 0, "text": f"x:{json.loads(u)['lines'][0]['text']}"}]})
    assert pipeline.stage_translate(project["id"], "en")["captions"][0]["text"] == "X:HELLO"


def test_translate_without_captions(project):
    import pytest
    with pytest.raises(ValueError):
        pipeline.stage_translate(project["id"], "fr")
