# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [SemVer](https://semver.org/).

## [Unreleased]

- UI and in-app messages now use neutral CLIP MASTER feature names. Third-party credits stay in NOTICE.md, the README credits section, and `third_party/`.

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
