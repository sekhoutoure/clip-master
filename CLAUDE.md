# CLAUDE.md

@AGENTS.md

## Claude Code specifics

- Skills for this repo are in `.claude/skills/` (same content as `skills/`): `install-clip-master`, `make-a-short-from-vod`,
  `batch-clips`, `export-presets`, `troubleshoot`. Use them when the user asks to install, run or debug CLIP MASTER.
- To install for a user who only gave you the repo URL: clone, run `./install.sh --yes` (or `.\install.ps1 -Yes` on Windows),
  then `./install.sh --check`, then tell them to run `./start.sh`. Do not stop at "pip install": the models and tools need the installer.
- Run `ruff check .` and `pytest -m "not slow"` before declaring a change done.
- Do not create `.env` with secrets for the user; the installer copies `.env.example` and the app works without a key.
