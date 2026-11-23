---
name: troubleshoot
description: Diagnose and fix common CLIP MASTER problems (install failures, FFmpeg or model errors, downloads that fail, AI mode not ready, jobs stuck, 403 on API calls). Use when something does not work or the user pastes an error.
---

# Troubleshoot CLIP MASTER

Start with the doctor: `./install.sh --check` (Windows `.\install.ps1 -Check`, or `clip-master doctor`). Apply the `->` hint of the first `[FAIL]` / `[warn]` line. Re-running the installer is always safe.

| Symptom | Cause | Fix |
|---|---|---|
| `Python 3.10-3.12 is required` / MediaPipe or torch wheel not found | Python too old or too new | Install Python 3.12, `./install.sh --python python3.12` (recreate `.venv`: delete it first) |
| `pip install` is huge / slow | CUDA PyTorch wheels | `./install.sh --cpu-torch` |
| `FFmpeg not found` or export fails with `Unknown filter 'subtitles'` | no FFmpeg or build without libass | `winget install Gyan.FFmpeg` / `brew install ffmpeg` / `sudo apt install ffmpeg`; or set `FFMPEG_PATH` in `.env` |
| `URL invalide` | only plain http(s) links are accepted | remove spaces and quotes, use the full link |
| `yt-dlp a échoué` / HTTP 403 / sign-in required | site blocks the download or needs login | `pip install -U yt-dlp` inside `.venv`; use a direct clip URL; or download the file yourself and upload the MP4 |
| Twitch VOD with chat fails | TwitchDownloaderCLI missing | `python bootstrap.py` (downloads it), or set `TWITCHDOWNLOADER_CLI` in `.env` |
| `streamlink n'a pas pu enregistrer` | channel is offline | the channel must be live |
| Badge says `Local · Ollama unavailable` | no `GROQ_API_KEY` and Ollama not running / model missing | start Ollama and `ollama pull qwen2.5:7b`, or put a Groq key in `.env`, or `LLM_PROVIDER=none` for heuristics only |
| Translation fails | LLM not ready (see above) | fix the AI mode; translation uses the same LLM |
| HTTP 403 `CSRF protection` on API calls | missing `Origin` header | send `Origin: http://127.0.0.1:7866` (match `CLIP_MASTER_HOST/PORT`) |
| HTTP 429 `Trop de tâches en cours` | one job (or the queue) is already running | wait, or check `GET /api/jobs/<id>` |
| `Fichier trop volumineux` | upload above the limit | raise `CLIP_MASTER_MAX_UPLOAD_MB` in `.env` |
| Download times out | long VOD | give `clip_start` / `clip_end`, or raise `CLIP_MASTER_DOWNLOAD_TIMEOUT` |
| Remotion render says dependencies are missing | Node.js < 18 or `npm install` not run | install Node 18+, `cd remotion && npm install` |
| Port 7866 already used | another instance | set `CLIP_MASTER_PORT` in `.env` |
| Queue item `error` | see its `error` text | fix the cause, then Retry |

## Logs and data

- Server log: the terminal running `./start.sh`. Jobs: `GET /api/jobs/<id>` (`error` field).
- Data: `projects/` (one folder per project), `outputs/` (exports), `models/` (weights). Safe to delete `projects/<id>` to free space.
- Run the checks: `.venv/bin/python -m pytest -m "not slow"`.
