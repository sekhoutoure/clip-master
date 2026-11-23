---
name: make-a-short-from-vod
description: Turn one long video (URL or local file) into a captioned 9:16 Short with CLIP MASTER, through the web UI or the HTTP API. Use when the user wants to clip a VOD, stream or video into a Short for TikTok, YouTube Shorts or Instagram Reels.
---

# Make a Short from a video

Only process content the user has the rights to (their own, licensed, or permitted by the creator and platform).

## Web UI (simplest)

1. Start the app (`./start.sh` / `start.bat`) and open http://127.0.0.1:7866.
2. Paste a link or drop an MP4 in **Import**.
3. Pick a **Style** (Standard = facecam + game, Tracked = follow the subject) and a **Platform** (TikTok, YouTube Shorts, Instagram Reels). Optional: choose a language in *Translate subtitles*.
4. Click **Create the Short**. The result is in `outputs/` and listed under **Publish** with a title and hashtags.

## HTTP API (for agents)

All mutating requests need `Origin: http://127.0.0.1:7866` (CSRF guard). Everything is asynchronous: you get a `job_id`, then poll `GET /api/jobs/{job_id}` until `status` is `done` or `error`.

```bash
O='Origin: http://127.0.0.1:7866'; H='Content-Type: application/json'
# 1. import (URL; use POST /api/upload with a multipart "file" for a local file)
curl -s -H "$O" -H "$H" -d '{"url":"https://example.com/video","source_type":"ytdlp","clip_start":"0:10:00","clip_end":"0:11:30"}' localhost:7866/api/pull
# -> {"job_id":"..."}   poll it; result.id is the project_id
# 2. whole pipeline: find -> track -> caption -> (translate) -> ship
curl -s -H "$O" -H "$H" -d '{"project_id":"proj_xxxx","export_preset":"tiktok","translate_to":"fr"}' localhost:7866/api/stage/full
# 3. download: result.export.filename -> GET /media/outputs/<filename>
```

Individual stages: `/api/stage/find|track|caption|translate|ship|batch-ship|remotion`. Tracked mode: pass `"tracked": {"enabled": true}` to `/api/stage/full` or `/api/stage/ship`.

## Rules of thumb

- Long VODs: always give `clip_start` / `clip_end` (`SS`, `MM:SS` or `HH:MM:SS`) so only the interesting part is downloaded.
- Live streams: `source_type: "streamlink"` records 5-120 s (`record_seconds`).
- `GET /api/mode` tells which AI mode is active (cloud Groq or local Ollama). Highlight picking falls back to audio-energy heuristics if the LLM is unavailable.
- If the job errors, read `error` in the job JSON and use the `troubleshoot` skill.
