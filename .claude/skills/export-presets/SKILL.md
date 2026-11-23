---
name: export-presets
description: Choose and apply CLIP MASTER export presets for TikTok, YouTube Shorts and Instagram Reels (aspect ratio, max duration, caption safe zones, caption styles). Use when the user asks which settings to use for a platform or wants captions kept inside the safe zone.
---

# Export presets

All presets export 1080x1920 (9:16), 30 fps, H.264 + AAC, -14 LUFS. They differ in length cap, safe zone and caption styles. List them live with `GET /api/presets`; the source of truth is `presets.py`.

| Preset id | Max length | Safe zone kept clear (top / bottom / left / right) | Caption styles (default first) |
|---|---|---|---|
| `tiktok` | 180 s | 10% / 22% / 6% / 14% | tiktok_white, tiktok_yellow, yellow, white |
| `youtube_shorts` | 180 s | 8% / 18% / 6% / 14% | white, yellow, cyan, tiktok_white |
| `instagram_reels` | 90 s | 14% / 24% / 6% / 12% | white, pink, yellow, tiktok_white |

The numbers are conservative community values. Platforms change them: edit `presets.py` (one place) and the tests in `tests/test_presets.py`.

## What a preset does

1. Caps the exported length (accounts for slow-motion: 90 s at 0.5x = 45 s of source).
2. Moves burned captions above the bottom unsafe zone.
3. Restricts / defaults the caption style to what suits the platform.
4. Records `preset` on the export entry in `project.json`.

## Use it

- UI: **Platform** selector in step 2.
- API: add `"export_preset": "tiktok" | "youtube_shorts" | "instagram_reels"` to `/api/stage/ship`, `/api/stage/batch-ship`, `/api/stage/full` or the queue `options`.
- Subtitles in another language: add `"translate_to": "fr"` (fr, en, es, de, pt, it, ar) to `/api/stage/full`, or call `/api/stage/translate` after captions exist. Uses Groq, or local Ollama without a key.

Unknown preset ids are rejected with HTTP 422.
