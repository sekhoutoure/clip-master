# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-11-23

### Security
- **Token authentication for network mode.** Listening on a non-loopback address (`CLIP_MASTER_HOST=0.0.0.0`, `::`, a LAN IP) now requires `CLIP_MASTER_TOKEN` (16+ characters); the server refuses to start without it, with a clear bilingual error. Every route (API, media, UI files) then needs `Authorization: Bearer <token>`, `X-Clip-Master-Token`, or the session cookie the web UI gets from the login form or a one-time `/?token=...` (HttpOnly, `SameSite=Strict`, holds a digest, not the token). `/api/health` stays open for the launchers. Loopback stays token-less (the token is still honoured when set). In network mode the browser origin of the server itself is accepted by the CSRF guard; scripts using the token header do not need `Origin`.
- **SSRF guard.** `validate_source_url` resolves the host and refuses private, loopback, link-local, reserved, shared (CGNAT) and multicast addresses for IPv4 and IPv6 (including IPv4-mapped IPv6 and numeric forms like `2130706433`), unless `CLIP_MASTER_ALLOW_PRIVATE_URLS=1`.
- CORS narrowed: no credentials, explicit methods and headers.
- Media routes answer 404 (not 500) for paths with NUL bytes.

### Changed
- README (EN/FR): the "nothing is uploaded" claim is replaced by what actually happens: no key = 100% local; with a Groq key the transcript text is sent to Groq. The UI shows a **Text sent to Groq** badge in that mode; `GET /api/mode` returns `sends_text_to_cloud`.
- The highlight director (`agents_crew.py`) takes its target window from the export preset (TikTok 15-60 s, YouTube Shorts 15-58 s, Instagram Reels 15-60 s; 8-16 s without a preset) instead of a hard-coded 8-16 s, clamps the LLM's answer to it, and exports with the preset. `POST /api/agents/run` accepts `export_preset`; the UI sends the selected platform.
- Backend error messages are bilingual (`messages.py`); the UI sends `X-Lang`, API clients can send `X-Lang` or `Accept-Language`. Background jobs keep the language of the request that started them.
- Logging is configured (`CLIP_MASTER_LOG_LEVEL`) with module loggers; the silent `except Exception: pass` blocks now log a warning with the traceback (per-frame tracking failures log at debug). `DELETE /api/outputs/<file>` reports `project_updated`.
- First step of splitting `pipeline.py`: `stage_05_ship` and the 9:16 encode helpers moved to `stages/ship.py`. `pipeline.stage_05_ship` and the other names are still importable from `pipeline`.
- README documents the `Origin` / token requirements for HTTP scripting, with `curl` examples.

### Added
- Reproducible installs: `requirements-lock/` (Linux x86_64, Windows x86_64, macOS arm64; Python 3.12; PyTorch is left out on purpose), `./install.sh --locked`, `scripts/update-lock.sh`, and a CI job that installs the lock and runs the tests.
- Tests: token auth, startup guard, SSRF filter, CORS, path traversal variants on `/media/...`, upload limits at the boundary, bilingual messages, logging, preset-driven window, import-order of the new module.
- UI and in-app messages now use neutral CLIP MASTER feature names. Third-party credits stay in NOTICE.md, the README credits section, and `third_party/`.
- Automatic cleanup of project workdirs, intermediate files and exports older than 48 hours (`CLIP_MASTER_CACHE_TTL_HOURS`, `0` disables the automatic pass). Runs at startup and periodically. A low-disk guard can refuse new jobs (`CLIP_MASTER_MIN_FREE_MB`). `POST /api/cache/clean`, a **Clear cache & old exports** / **Vider le cache & anciens exports** button, and `clip-master clean`. The current job and the queue file are never deleted.
- CUDA is autodetected at startup (`CLIP_MASTER_DEVICE=auto|cpu|cuda`). Transcription uses GPU float16 and subject tracking uses YOLO `device=0` when CUDA is available, otherwise CPU int8. Exposed in `GET /api/mode` and `clip-master doctor`. DirectML is not auto-enabled.
- README pipeline diagram (Ingest → Find highlights → Track facecam → Captions → Export). `.env.example` lists every expected variable, with no real secrets.

## [0.1.0] - 2026-11-23

First public version of CLIP MASTER.

### Added
- Local 9:16 Shorts pipeline: import (URL, live record, Twitch VOD, local file), scene/silence/highlight detection, MediaPipe + YOLO tracking, faster-whisper captions, FFmpeg export with loudness normalisation and QC.
- "Tracked" mode: YOLO-followed crop with facecam inset and pop captions.
- Export presets for TikTok, YouTube Shorts and Instagram Reels (max length, caption safe zones, caption styles).
- Batch queue with cancel / retry, persisted across restarts.
- French / English UI with a language toggle (`static/i18n/`), and LLM subtitle translation (fr, en, es, de, pt, it, ar).
- No-key mode: falls back to local Ollama when `GROQ_API_KEY` is empty (`LLM_PROVIDER` to force a mode); transcription is always local. AI mode badge in the UI and `GET /api/mode`.
- Cross-platform installer (`install.sh`, `install.ps1`, `start.bat`, `start.sh`) with a doctor (`--check`), `third_party/manifest.yml` for pinned third-party tools, `clip-master` CLI (`serve`, `doctor`, `install`).
- `AGENTS.md`, `CLAUDE.md`, Cursor rule and agent skills (`skills/`, `.claude/skills/`).
- pytest suite using FFmpeg-generated media, ruff lint, GitHub Actions CI and release workflows.

### Security
- Only plain http(s) URLs reach yt-dlp / streamlink / TwitchDownloader; `--` is passed before URLs; time ranges are validated; no shell execution.
- Configurable upload size limit, strict CSRF origin guard, local-only "open outputs folder".

[Unreleased]: https://github.com/sekhoutoure/clip-master/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sekhoutoure/clip-master/releases/tag/v0.1.0
