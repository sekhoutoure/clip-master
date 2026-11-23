"""Shared fixtures. All data lives in a temp dir; media is generated with FFmpeg (no third-party content)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_TMP = Path(tempfile.mkdtemp(prefix="clipmaster_test_"))
os.environ["CLIP_MASTER_DATA_DIR"] = str(_TMP)
os.environ["CLIP_MASTER_PORT"] = "7866"
os.environ["CLIP_MASTER_HOST"] = "127.0.0.1"
os.environ["GROQ_API_KEY"] = ""
os.environ["LLM_PROVIDER"] = "auto"
os.environ.setdefault("CLIP_MASTER_CACHE_SWEEP_SECONDS", "0")
os.environ.setdefault("CLIP_MASTER_MIN_FREE_MB", "0")
os.environ.setdefault("CLIP_MASTER_CACHE_TTL_HOURS", "48")
sys.path.insert(0, str(ROOT))

ORIGIN = {"Origin": "http://127.0.0.1:7866"}


@pytest.fixture(autouse=True)
def _fake_public_dns(monkeypatch):
    """Tests never touch real DNS: every host name resolves to a documentation-range *public-looking* address.

    (TEST-NET ranges are not "global", so use a real public address that is never contacted.)
    Tests that exercise the SSRF filter override ``security._getaddrinfo`` themselves.
    """
    import security

    def fake(host):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(security, "_getaddrinfo", fake)


def _ffmpeg() -> str:
    from tools_registry import get_ffmpeg_path
    return get_ffmpeg_path()


@pytest.fixture(scope="session")
def sample_video() -> Path:
    """6 s 640x360 test pattern + two tones (generated, neutral)."""
    out = _TMP / "sample.mp4"
    if not out.exists():
        subprocess.run(
            [_ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=6",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(out)],
            check=True, capture_output=True,
        )
    return out


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    import server
    with TestClient(server.app, base_url="http://127.0.0.1:7866", headers=ORIGIN) as c:
        yield c


@pytest.fixture()
def project(sample_video):
    """A project created through the real local-import stage."""
    import pipeline
    return pipeline.stage_01_pull("local", local_file=sample_video, title="TEST")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
