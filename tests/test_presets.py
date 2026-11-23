import pytest

import presets


def test_three_platform_presets():
    assert set(presets.PRESETS) == {"tiktok", "youtube_shorts", "instagram_reels"}
    for pid, p in presets.PRESETS.items():
        assert (p["width"], p["height"], p["fps"]) == (1080, 1920, 30)
        assert p["default_caption_style"] in p["caption_styles"]
        assert set(p["caption_styles"]) <= set(presets.ALL_CAPTION_STYLES)
        sz = p["safe_zone"]
        assert all(0 <= v < 0.5 for v in sz.values())
        assert 0.3 < presets.caption_ypos(pid) <= 0.66
        assert presets.caption_margin_v(pid) > 1920 * sz["bottom"]


def test_unknown_preset():
    with pytest.raises(ValueError):
        presets.get_preset("vimeo")
    assert presets.get_preset(None) is None


def test_clamp_window():
    assert presets.clamp_window(None, 0, 500) == (0, 500, False)
    assert presets.clamp_window("instagram_reels", 10, 60) == (10, 60, False)
    s, e, trimmed = presets.clamp_window("instagram_reels", 10, 300)
    assert (s, e, trimmed) == (10, 100, True)
    # slow-mo 0.5x: 90 s of output allows only 45 s of source
    assert presets.clamp_window("instagram_reels", 0, 300, speed=0.5)[1] == 45


def test_caption_style_resolution():
    assert presets.resolve_caption_style("tiktok", "yellow") == "yellow"
    assert presets.resolve_caption_style("tiktok", "cyan") == "tiktok_white"
    assert presets.resolve_caption_style(None, "cyan") == "cyan"


def test_presets_endpoint(client):
    d = client.get("/api/presets").json()
    assert {p["id"] for p in d["presets"]} == set(presets.PRESETS)
    assert "tiktok_white" in d["caption_styles"]


def test_ship_rejects_unknown_preset(client):
    r = client.post("/api/stage/ship", json={"project_id": "proj_x", "export_preset": "vimeo"})
    assert r.status_code == 422
