---
name: install-clip-master
description: Install and verify CLIP MASTER (local 9:16 Shorts studio) on Windows, macOS or Linux from a git clone, including FFmpeg, model weights and third-party tools. Use when the user gives the repo URL or asks to set up, update or repair the installation.
---

# Install CLIP MASTER

Goal: a working install that passes `--check`, reached with the fewest steps. Never stop at `pip install`.

## Steps

1. Clone (skip if already inside the repo):
   `git clone <REPO_URL> clip-master && cd clip-master`
2. Run the installer (it is idempotent: re-run after any failure).
   - Linux / macOS: `./install.sh --yes`
   - Windows PowerShell: `.\install.ps1 -Yes`
   - No GPU: add `--cpu-torch` (`-CpuTorch` on Windows) for a ~200 MB PyTorch instead of ~2 GB.
   - Need the reference-only sources too: add `--with-reference` (`-WithReference`).
   - `--yes` lets it install missing ffmpeg / Node.js with the system package manager (apt, dnf, pacman, brew, winget). Without `--yes` it prints the command instead.
3. Verify: `./install.sh --check` (Windows: `.\install.ps1 -Check`). Exit code 0 means every required check passed.
   For parsing: `.venv/bin/python bootstrap.py --json` returns `{ "ok": bool, "checks": [{status, name, detail, fix}] }`.
4. Start: `./start.sh` (Windows: `start.bat`), open http://127.0.0.1:7866.
5. Optional: edit `.env` (created from `.env.example`). `GROQ_API_KEY` is optional. Without it, install Ollama and run `ollama pull qwen2.5:7b` for the AI features; transcription is always local.

## Requirements the installer checks

Python 3.10-3.12 (3.13 works but some vision / PyTorch wheels may be missing), FFmpeg with libass (a bundled fallback comes from imageio-ffmpeg), Node.js 18+ only for animated captions, internet for the first run (pip, model weights, optional VOD+chat CLI).

## What it fetches

`third_party/manifest.yml` lists every external tool with license, pinned tag or commit and purpose. Binaries go to the git-ignored `third_party/` folder with a checksum check. Nothing is vendored into git. Do not add projects without a license.

## If it fails

Read the last `error:` or `[FAIL]` line, apply the `->` hint under it, re-run. See the `troubleshoot` skill for the usual causes.
