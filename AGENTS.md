# AGENTS.md - CLIP MASTER

Instructions for coding agents (Claude Code, Cursor, Codex, Copilot, Aider, ...). Humans: see `README.md`.

CLIP MASTER is a local FastAPI app that turns a long video (URL or file) into a captioned 9:16 Short for
TikTok, YouTube Shorts or Instagram Reels. Python 3.10-3.12, FFmpeg, optional Node.js (Remotion captions).

## Install (copy-paste, non-interactive)

```bash
# Linux / macOS
git clone <REPO_URL> clip-master && cd clip-master
./install.sh --yes            # venv + pip deps + ffmpeg/node (if missing) + models + third-party tools
./install.sh --check          # doctor: exit code 0 = all required checks pass
./start.sh                    # serves http://127.0.0.1:7866
```

```powershell
# Windows (PowerShell)
git clone <REPO_URL> clip-master; cd clip-master
.\install.ps1 -Yes
.\install.ps1 -Check
.\start.bat
```

- The installer is idempotent: re-run it after any failure. Read its last `error:` / `[FAIL]` line and fix that first.
- `./install.sh --check` prints one line per check; add `--json` through `python bootstrap.py --json` for machine-readable output.
- Add `--cpu-torch` on machines without a GPU (small PyTorch), `--with-reference` to also clone reference-only sources.
- No API key is needed. Without `GROQ_API_KEY` the AI features use local Ollama (`ollama pull qwen2.5:7b`); transcription is always local.
- Never commit `.env`, `projects/`, `outputs/`, `models/`, `third_party/*` (all git-ignored).

## Verify your work

```bash
.venv/bin/python -m ruff check .          # lint (must be clean)
.venv/bin/python -m pytest -m "not slow"  # fast tests, ~20 s, needs ffmpeg + ffprobe on PATH
.venv/bin/python -m pytest                # everything (downloads yolov8n.pt on first run)
```

Tests generate their own media with FFmpeg and use a temp data dir; they never touch real projects.
Install dev tools with `.venv/bin/python -m pip install -r requirements-dev.txt`.

## Map of the code

| File | Role |
|---|---|
| `server.py` | FastAPI routes, job runner, CSRF origin guard, batch queue endpoints |
| `pipeline.py` | Stages: `stage_01_pull`, `02_find`, `03_track`, `04_caption`, `stage_translate`, `05_ship` (+ batch, Remotion) |
| `tracked_render.py` | "Tracked" mode: YOLO-followed crop, facecam inset, captions, QC |
| `presets.py` | TikTok / YouTube Shorts / Instagram Reels export presets (max length, safe zones, caption styles) |
| `batch_queue.py` | Persistent queue (`projects/_queue.json`): cancel, retry, resume after restart |
| `llm.py` | Groq or local Ollama, `translate_captions`, `llm_status` (the "no-key mode") |
| `agents_crew.py` | LLM "AI crew": highlight director, caption copywriter |
| `qc.py`, `audio_align.py`, `profanity.py` | Export QC, audio alignment, optional caption filter |
| `tools_registry.py` | Tool detection (ffmpeg, TwitchDownloaderCLI, model weights) |
| `bootstrap.py` | Installer logic + doctor; `third_party/manifest.yml` is its manifest |
| `clip_master_cli.py` | `clip-master serve | doctor | install` |
| `static/` | Web UI (`index.html`, `app.js`, `features.js`, `i18n.js`, `i18n/{fr,en}.json`) |
| `remotion/` | Optional animated captions (React). `npm install` inside it |

## Rules for changes

- Keep it generic: no campaign, channel or brand-specific code, presets or assets.
- Every user-visible string in the UI goes through `data-i18n` / `t()` and exists in **both** `static/i18n/fr.json` and `en.json` (a test enforces it).
- Validate anything that reaches a subprocess: URLs through `pipeline.validate_source_url`, times through `validate_time_spec`; pass `--` before URLs; never use `shell=True`.
- Mutating API calls need an `Origin` header equal to the server origin (CSRF guard). Tests use the `client` fixture which sets it.
- New third-party tool: add it to `third_party/manifest.yml` (license, pinned ref) and `NOTICE.md`. Do not vendor code. Skip projects with no license.
- Add or update tests for behavior you change. Update `CHANGELOG.md` (Unreleased section).
- Python style: ruff, type hints on new functions, `pathlib.Path`, no new global state without a lock.

## Skills (task recipes)

Agent skills live in `skills/` (mirrored in `.claude/skills/` for Claude Code). Read the matching `SKILL.md` before the task:

- `skills/install-clip-master/` - install and verify on any OS
- `skills/make-a-short-from-vod/` - one video to one Short (UI or API)
- `skills/batch-clips/` - queue many videos
- `skills/export-presets/` - TikTok / Shorts / Reels settings
- `skills/troubleshoot/` - diagnosis table for common failures

## Legal note for users of the tool

Only process content you have the rights to. Do not add features that strip watermarks or evade platform rules.
