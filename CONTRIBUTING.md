# Contributing to CLIP MASTER

Thanks for helping! Small, focused pull requests are easiest to review.

## Set up

```bash
git clone <your fork> && cd clip-master
./install.sh --yes --cpu-torch        # Windows: .\install.ps1 -Yes -CpuTorch
.venv/bin/python -m pip install -r requirements-dev.txt
./install.sh --check
```

## Before you open a PR

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -m "not slow"     # fast, generates its own test media with FFmpeg
.venv/bin/python -m pytest                   # includes tests that download model weights
```

- Add or update tests for what you change (`tests/`). Tests use a temp data directory and never touch your real projects.
- UI text: add the key to **both** `static/i18n/fr.json` and `static/i18n/en.json`, use `data-i18n="key"` in HTML or `t('key')` in JS.
- Anything that reaches a subprocess must be validated (see `pipeline.validate_source_url`); never use `shell=True`.
- Update `CHANGELOG.md` under **Unreleased**.

## Third-party code and licenses

CLIP MASTER is AGPL-3.0. Do not copy third-party source into the repo. To use a new tool: depend on it (`requirements.txt`) or fetch it through `third_party/manifest.yml` with a pinned ref, and add it to `NOTICE.md` with its license. Projects without a license cannot be used.

## Content rules

Do not add test media you do not own (no clips from streamers, games or films). Generated media only.
No feature whose purpose is to strip watermarks, evade platform rules or hide that content was repurposed.

## Releases (maintainers)

Bump `version` in `pyproject.toml` and `clip_master_cli.py`, move the Unreleased notes under the new version in `CHANGELOG.md`, then `git tag vX.Y.Z && git push --tags`. The Release workflow tests, builds and publishes.

## Conduct

Be kind and constructive. Harassment is not tolerated.
