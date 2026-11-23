"""Export presets for TikTok, YouTube Shorts and Instagram Reels.

All three platforms take 1080x1920 (9:16). The presets differ in the maximum clip length we allow,
the caption *safe zone* (the part of the frame not covered by the app's own buttons, title and
progress bar) and the caption styles that look right there.

The numbers are conservative community-documented values; platforms change them, so every field can be
overridden by the caller and the limits live in one place (this file).
Safe-zone values are fractions of the frame: ``top``/``bottom``/``left``/``right`` margins to keep clear.
"""
from __future__ import annotations

from typing import Any

WIDTH, HEIGHT, FPS = 1080, 1920, 30
ALL_CAPTION_STYLES = ("pink", "yellow", "cyan", "white", "tiktok_white", "tiktok_yellow")

PRESETS: dict[str, dict[str, Any]] = {
    "tiktok": {
        "label": "TikTok",
        "aspect": "9:16", "width": WIDTH, "height": HEIGHT, "fps": FPS,
        "max_duration": 180,
        "safe_zone": {"top": 0.10, "bottom": 0.22, "left": 0.06, "right": 0.14},
        "caption_styles": ["tiktok_white", "tiktok_yellow", "yellow", "white"],
        "default_caption_style": "tiktok_white",
    },
    "youtube_shorts": {
        "label": "YouTube Shorts",
        "aspect": "9:16", "width": WIDTH, "height": HEIGHT, "fps": FPS,
        "max_duration": 180,
        "safe_zone": {"top": 0.08, "bottom": 0.18, "left": 0.06, "right": 0.14},
        "caption_styles": ["white", "yellow", "cyan", "tiktok_white"],
        "default_caption_style": "white",
    },
    "instagram_reels": {
        "label": "Instagram Reels",
        "aspect": "9:16", "width": WIDTH, "height": HEIGHT, "fps": FPS,
        "max_duration": 90,
        "safe_zone": {"top": 0.14, "bottom": 0.24, "left": 0.06, "right": 0.12},
        "caption_styles": ["white", "pink", "yellow", "tiktok_white"],
        "default_caption_style": "white",
    },
}


def list_presets() -> list[dict[str, Any]]:
    """Presets with their id, ready for the UI."""
    out = []
    for pid, p in PRESETS.items():
        out.append({"id": pid, **p, "caption_ypos": caption_ypos(pid), "margin_v": caption_margin_v(pid)})
    return out


def get_preset(preset_id: str | None) -> dict[str, Any] | None:
    if not preset_id:
        return None
    if preset_id not in PRESETS:
        raise ValueError(f"Unknown export preset {preset_id!r} (choose: {', '.join(PRESETS)})")
    return PRESETS[preset_id]


def caption_margin_v(preset_id: str | None) -> int:
    """ASS bottom margin (pixels on the 1080x1920 canvas) that keeps bottom-anchored captions inside the safe zone."""
    p = get_preset(preset_id)
    if not p:
        return 0
    return int(HEIGHT * p["safe_zone"]["bottom"]) + 20


def caption_ypos(preset_id: str | None) -> float:
    """Vertical centre (fraction of height) for centre-anchored captions, a bit above the bottom unsafe zone."""
    p = get_preset(preset_id)
    if not p:
        return 0.66
    return round(min(0.66, 1.0 - p["safe_zone"]["bottom"] - 0.08), 3)


def clamp_window(preset_id: str | None, start: float, end: float, speed: float = 1.0) -> tuple[float, float, bool]:
    """Shorten [start, end] (source seconds) so the *output* duration (after speed change) respects the preset.

    Returns (start, end, was_trimmed).
    """
    p = get_preset(preset_id)
    if not p:
        return start, end, False
    max_src = float(p["max_duration"]) * max(0.25, speed)
    if end - start <= max_src:
        return start, end, False
    return start, start + max_src, True


def resolve_caption_style(preset_id: str | None, requested: str | None) -> str:
    """Use the requested style when the preset allows it, else the preset default (or the request when no preset)."""
    p = get_preset(preset_id)
    if not p:
        return requested or "pink"
    if requested in p["caption_styles"]:
        return requested
    return p["default_caption_style"]
