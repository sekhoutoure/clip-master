import pytest

import pipeline
from tests.test_pipeline import probe


def test_find_returns_cuts_and_highlight(project):
    p = pipeline.stage_02_find(project["id"])
    assert "find" in p["stage_completed"]
    hl = p["highlight"]
    assert 0 <= hl["start"] < hl["end"] <= p["info"]["duration"] + 0.01
    assert p["moments"]


@pytest.mark.slow
def test_track_then_tracked_ship_with_preset(project):
    p = pipeline.stage_03_track(project["id"])
    assert len(p["face_track"]) > 0 and "track" in p["stage_completed"]
    p = pipeline.load_project(project["id"])
    p["captions"] = [{"id": "c1", "text": "TRACKED TEST", "start": 0.5, "end": 3.0, "color": "yellow"}]
    pipeline.save_project(project["id"], p)
    res = pipeline.stage_05_ship(project["id"], tracked={"enabled": True}, export_preset="instagram_reels",
                                 start_sec=0, end_sec=5)
    out = pipeline.OUTPUTS_DIR / res["export"]["filename"]
    v = next(s for s in probe(out)["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert res["export"]["tracked"]["report"]["qc"]["width"] == 1080
