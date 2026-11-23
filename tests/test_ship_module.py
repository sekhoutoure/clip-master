"""stage_05_ship now lives in stages/ship.py; pipeline keeps re-exporting it (backward compatible)."""
import subprocess
import sys
from pathlib import Path

import pytest

import pipeline
from stages import ship

ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_reexports_the_same_objects():
    for name in ("stage_05_ship", "_render_vertical", "_build_vertical_filter", "_atempo_chain", "_ffmpeg_path_escape"):
        assert getattr(pipeline, name) is getattr(ship, name), name


@pytest.mark.parametrize("first", ["pipeline", "stages.ship", "server", "agents_crew", "tracked_render"])
def test_import_order_has_no_cycle(first, tmp_path):
    env = {"CLIP_MASTER_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    code = f"import {first}; import pipeline, stages.ship; assert pipeline.stage_05_ship is stages.ship.stage_05_ship"
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**env, "HOME": str(tmp_path)}, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr


def test_ship_looks_up_shared_helpers_on_pipeline(project, monkeypatch):
    """Helpers that stay in pipeline are resolved at call time, so patching them there still takes effect."""
    calls = []
    real = pipeline.generate_viral_kit
    monkeypatch.setattr(pipeline, "generate_viral_kit", lambda p: (calls.append(1), real(p))[1])
    res = pipeline.stage_05_ship(project["id"], layout_mode="CENTER CROP", normalize_audio=False, retention_bar=False,
                                 interpolate_slowmo=False, start_sec=0, end_sec=2)
    assert calls and (pipeline.OUTPUTS_DIR / res["export"]["filename"]).exists()


def test_ship_rejects_unknown_preset_early(project):
    with pytest.raises(ValueError):
        pipeline.stage_05_ship(project["id"], export_preset="vimeo")


def test_packaging_lists_new_modules():
    text = (ROOT / "pyproject.toml").read_text()
    for mod in ("messages", "security"):
        assert f'"{mod}"' in text
    assert 'packages = ["stages"]' in text
    assert (ROOT / "stages" / "__init__.py").exists()
