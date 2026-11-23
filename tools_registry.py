"""Registry of the pipeline features CLIP MASTER runs (credits and licenses: NOTICE.md).

integration:
  "executed" — this feature runs in the pipeline (pip package, CLI or binary)
"""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path
from typing import Any

from config import BASE_DIR, BIN_DIR, FFMPEG_PATH, MODELS_DIR, TWITCHDOWNLOADER_CLI

YOLO_WEIGHTS_NAME = "yolov8n.pt"
FACE_MODEL_NAME = "blaze_face_short_range.tflite"
FACE_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
                  "blaze_face_short_range/float16/1/blaze_face_short_range.tflite")


TOOLS_MANIFEST: list[dict[str, Any]] = [
    # Display name / role / engine_note are neutral feature labels shown in the UI.
    # Package and CLI names stay in py_module / cli for runtime detection only.
    # Credits and licenses live in NOTICE.md, not in this list.
    {
        "id": "01",
        "name": "Download",
        "stage": "01 / PULL",
        "stage_key": "pull",
        "role": "Download a video from a link",
        "integration": "executed",
        "engine_note": "Used when you paste a URL",
        "py_module": "yt_dlp",
        "cli": "yt-dlp",
    },
    {
        "id": "02",
        "name": "Live record",
        "stage": "01 / PULL",
        "stage_key": "pull",
        "role": "Record a live stream to a file",
        "integration": "executed",
        "engine_note": "Used when you record a live channel",
        "py_module": "streamlink",
        "cli": "streamlink",
    },
    {
        "id": "03",
        "name": "VOD + chat",
        "stage": "01 / PULL",
        "stage_key": "pull",
        "role": "Download a VOD together with its chat",
        "integration": "executed",
        "engine_note": "Optional: used for a Twitch VOD + chat (see .env.example)",
        "py_module": None,
        "cli": "TwitchDownloaderCLI",
    },
    {
        "id": "04",
        "name": "Scene cuts",
        "stage": "02 / FIND",
        "stage_key": "find",
        "role": "Find cuts and scene changes",
        "integration": "executed",
        "engine_note": "Runs during the find stage",
        "py_module": "scenedetect",
        "cli": "scenedetect",
    },
    {
        "id": "05",
        "name": "Silence trim",
        "stage": "02 / FIND",
        "stage_key": "find",
        "role": "Strip dead air and silence",
        "integration": "executed",
        "engine_note": "Used when silence removal is checked",
        "py_module": "auto_editor",
        "cli": "auto-editor",
    },
    {
        "id": "08",
        "name": "Facecam",
        "stage": "03 / TRACK",
        "stage_key": "track",
        "role": "Detect and track the facecam",
        "integration": "executed",
        "engine_note": "Runs during the track stage",
        "py_module": "mediapipe",
        "cli": None,
    },
    {
        "id": "09",
        "name": "Subject tracking",
        "stage": "03 / TRACK",
        "stage_key": "track",
        "role": "Follow the subject for the 9:16 crop",
        "integration": "executed",
        "engine_note": "Runs during the track stage",
        "py_module": "ultralytics",
        "cli": "yolo",
    },
    {
        "id": "12",
        "name": "Transcription",
        "stage": "04 / CAPTION",
        "stage_key": "caption",
        "role": "Local word-level transcription",
        "integration": "executed",
        "engine_note": "Runs during the caption stage, no API key needed",
        "py_module": "faster_whisper",
        "cli": None,
    },
    {
        "id": "14",
        "name": "Voice split",
        "stage": "05 / SHIP",
        "stage_key": "ship",
        "role": "Split the voice from the background music",
        "integration": "executed",
        "engine_note": "Used when voice split is checked",
        "py_module": "demucs",
        "cli": "demucs",
    },
    {
        "id": "18",
        "name": "Animated captions",
        "stage": "04 / CAPTION",
        "stage_key": "caption",
        "role": "Optional animated captions",
        "integration": "executed",
        "engine_note": "Used when you render animated captions",
        "py_module": None,
        "cli": "remotion",
    },
    {
        "id": "20",
        "name": "Export",
        "stage": "05 / SHIP",
        "stage_key": "ship",
        "role": "Encode the final 9:16 export",
        "integration": "executed",
        "engine_note": "Encodes every 9:16 export (H.264, captions, loudness)",
        "py_module": "imageio_ffmpeg",
        "cli": "ffmpeg",
    },
]


def _exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def get_ffmpeg_path() -> str:
    """FFMPEG_PATH (.env) > bin/ffmpeg > PATH > imageio-ffmpeg."""
    if FFMPEG_PATH and Path(FFMPEG_PATH).exists():
        return FFMPEG_PATH
    local_bin = BIN_DIR / _exe("ffmpeg")
    if local_bin.exists():
        return str(local_bin)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def get_twitch_cli() -> str | None:
    """TWITCHDOWNLOADER_CLI (.env) > third_party/ (installer) > bin/ > PATH. None when not installed."""
    if TWITCHDOWNLOADER_CLI and Path(TWITCHDOWNLOADER_CLI).exists():
        return TWITCHDOWNLOADER_CLI
    for folder in (BASE_DIR / "third_party" / "TwitchDownloaderCLI", BIN_DIR):
        local = folder / _exe("TwitchDownloaderCLI")
        if local.exists():
            return str(local)
    return shutil.which("TwitchDownloaderCLI")


def get_yolo_weights_path() -> Path:
    return MODELS_DIR / YOLO_WEIGHTS_NAME


def get_face_model_path() -> Path:
    return MODELS_DIR / FACE_MODEL_NAME


def ensure_face_model() -> Path | None:
    """Download the face-detection model on first use. Returns None if unavailable."""
    path = get_face_model_path()
    if path.exists():
        return path
    try:
        import urllib.request

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(FACE_MODEL_URL, tmp)  # noqa: S310 (fixed https URL)
        tmp.replace(path)
        return path
    except Exception:
        return None


def check_tools_status() -> list[dict[str, Any]]:
    results = []
    for item in TOOLS_MANIFEST:
        runtime_ready = False
        if item["id"] == "08":
            runtime_ready = importlib.util.find_spec("mediapipe") is not None
        elif item["id"] == "09":
            runtime_ready = importlib.util.find_spec("ultralytics") is not None
        elif item["id"] == "03":
            runtime_ready = get_twitch_cli() is not None
        elif item["id"] == "18":
            runtime_ready = (BASE_DIR / "remotion" / "node_modules" / "@remotion" / "cli").exists()
        elif item["id"] == "20":
            runtime_ready = os.path.exists(get_ffmpeg_path()) or shutil.which("ffmpeg") is not None
        else:
            if item["py_module"]:
                runtime_ready = importlib.util.find_spec(item["py_module"]) is not None
            if not runtime_ready and item["cli"]:
                runtime_ready = shutil.which(item["cli"]) is not None

        executed = item["integration"] == "executed"
        results.append(
            {
                **item,
                "runtime_ready": runtime_ready,
                # "ready" = this feature is installed and will run.
                "ready": bool(executed and runtime_ready),
                "status": (
                    "executed" if executed and runtime_ready
                    else "executed_missing" if executed
                    else "equivalent"
                ),
            }
        )
    return results
