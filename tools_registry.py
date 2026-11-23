"""Registry of the third-party tools CLIP MASTER builds on (see NOTICE.md for licenses).

integration:
  "executed"   - the tool itself runs in the pipeline (pip package, CLI or binary)
  "equivalent" - the idea is re-implemented in-house (usually FFmpeg or the LLM director); nothing is bundled
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
    {
        "id": "01",
        "name": "yt-dlp",
        "stage": "01 / PULL",
        "stage_key": "pull",
        "github": "https://github.com/yt-dlp/yt-dlp",
        "role": "Pull VODs and clips from almost any site",
        "integration": "executed",
        "engine_note": "CLI yt-dlp invoked directly in stage_01_pull",
        "py_module": "yt_dlp",
        "cli": "yt-dlp",
    },
    {
        "id": "02",
        "name": "streamlink",
        "stage": "01 / PULL",
        "stage_key": "pull",
        "github": "https://github.com/streamlink/streamlink",
        "role": "Record a live stream straight to a file",
        "integration": "executed",
        "engine_note": "CLI streamlink invoked directly in stage_01_pull",
        "py_module": "streamlink",
        "cli": "streamlink",
    },
    {
        "id": "03",
        "name": "TwitchDownloader",
        "stage": "01 / PULL",
        "stage_key": "pull",
        "github": "https://github.com/lay295/TwitchDownloader",
        "role": "Grab Twitch VODs with the chat",
        "integration": "executed",
        "engine_note": "TwitchDownloaderCLI (optional binary, see .env.example) invoked for VOD + chatdownload",
        "py_module": None,
        "cli": "TwitchDownloaderCLI",
    },
    {
        "id": "04",
        "name": "PySceneDetect",
        "stage": "02 / FIND",
        "stage_key": "find",
        "github": "https://github.com/Breakthrough/PySceneDetect",
        "role": "Find every cut and scene change",
        "integration": "executed",
        "engine_note": "scenedetect.detect(ContentDetector) invoked in stage_02_find",
        "py_module": "scenedetect",
        "cli": "scenedetect",
    },
    {
        "id": "05",
        "name": "auto-editor",
        "stage": "02 / FIND",
        "stage_key": "find",
        "github": "https://github.com/WyattBlue/auto-editor",
        "role": "Strip dead air and silence",
        "integration": "executed",
        "engine_note": "CLI auto-editor invoked when 'auto-editor silence' is checked",
        "py_module": "auto_editor",
        "cli": "auto-editor",
    },
    {
        "id": "06",
        "name": "FunClip",
        "stage": "02 / FIND",
        "stage_key": "find",
        "github": "https://github.com/modelscope/FunClip",
        "role": "Pick highlights from the transcript with an LLM",
        "integration": "equivalent",
        "engine_note": "Inspired by it (re-implemented in-house); replaced by Groq LLM (or Ollama fallback) highlight director in agents_crew.py",
        "py_module": None,
        "cli": None,
    },
    {
        "id": "07",
        "name": "ClipsAI",
        "stage": "02 / FIND",
        "stage_key": "find",
        "github": "https://github.com/ClipsAI/clipsai",
        "role": "Turn long streams into clips and reframe to 9:16",
        "integration": "equivalent",
        "engine_note": "Inspired by it (re-implemented in-house); top-3 non-overlapping window picker + 9:16 crop reframing in pipeline.py",
        "py_module": None,
        "cli": None,
    },
    {
        "id": "08",
        "name": "MediaPipe",
        "stage": "03 / TRACK",
        "stage_key": "track",
        "github": "https://github.com/google-ai-edge/mediapipe",
        "role": "Detect and track the facecam face",
        "integration": "executed",
        "engine_note": "mediapipe.tasks.python.vision.FaceDetector (blaze_face_short_range.tflite) in stage_03_track",
        "py_module": "mediapipe",
        "cli": None,
    },
    {
        "id": "09",
        "name": "Ultralytics",
        "stage": "03 / TRACK",
        "stage_key": "track",
        "github": "https://github.com/ultralytics/ultralytics",
        "role": "Track the character in gameplay",
        "integration": "executed",
        "engine_note": "Ultralytics YOLOv8n (yolov8n.pt) person tracking in stage_03_track",
        "py_module": "ultralytics",
        "cli": "yolo",
    },
    # Note: 10_SAM2 (facebookresearch/sam2) was removed from the active manifest:
    # it requires PyTorch CUDA + heavy checkpoints (not available on this CPU setup)
    # and is not wired into pipeline.py.
    {
        "id": "11",
        "name": "WhisperX",
        "stage": "04 / CAPTION",
        "stage_key": "caption",
        "github": "https://github.com/m-bain/whisperX",
        "role": "Word-level timestamps for captions",
        "integration": "equivalent",
        "engine_note": "Inspired by it (re-implemented in-house); word-level timestamps provided by faster-whisper word_timestamps=True + VAD",
        "py_module": "whisperx",
        "cli": None,
    },
    {
        "id": "12",
        "name": "faster-whisper",
        "stage": "04 / CAPTION",
        "stage_key": "caption",
        "github": "https://github.com/SYSTRAN/faster-whisper",
        "role": "Fast transcription on your own machine",
        "integration": "executed",
        "engine_note": "faster_whisper.WhisperModel(int8, word_timestamps=True) invoked in stage_04_caption",
        "py_module": "faster_whisper",
        "cli": None,
    },
    {
        "id": "13",
        "name": "auto-subtitle",
        "stage": "04 / CAPTION",
        "stage_key": "caption",
        "github": "https://github.com/m1guelpf/auto-subtitle",
        "role": "Burn subtitles onto the video",
        "integration": "equivalent",
        "engine_note": "Inspired by it (re-implemented in-house); generates SRT + styled ASS burned via FFmpeg libass filter",
        "py_module": None,
        "cli": None,
    },
    {
        "id": "14",
        "name": "Demucs",
        "stage": "05 / SHIP",
        "stage_key": "ship",
        "github": "https://github.com/facebookresearch/demucs",
        "role": "Split the streamer's voice from game music",
        "integration": "executed",
        "engine_note": "python -m demucs --two-stems=vocals invoked when 'Demucs vocals' is checked",
        "py_module": "demucs",
        "cli": "demucs",
    },
    {
        "id": "15",
        "name": "Video2X",
        "stage": "05 / SHIP",
        "stage_key": "ship",
        "github": "https://github.com/k4yt3x/video2x",
        "role": "Upscale low-res stream footage",
        "integration": "equivalent",
        "engine_note": "Inspired by it (re-implemented in-house); CPU export uses FFmpeg Lanczos 1080x1920 + unsharp luma/chroma filter",
        "py_module": None,
        "cli": "video2x",
    },
    {
        "id": "16",
        "name": "Practical-RIFE",
        "stage": "05 / SHIP",
        "stage_key": "ship",
        "github": "https://github.com/hzwer/Practical-RIFE",
        "role": "Smooth slow-mo for the big moment",
        "integration": "equivalent",
        "engine_note": "Inspired by it (re-implemented in-house); CPU slow-mo uses FFmpeg motion-compensated minterpolate + synced atempo audio",
        "py_module": None,
        "cli": None,
    },
    {
        "id": "17",
        "name": "MoviePy",
        "stage": "05 / SHIP",
        "stage_key": "ship",
        "github": "https://github.com/Zulko/moviepy",
        "role": "Stitch the cuts together in Python",
        "integration": "equivalent",
        "engine_note": "Inspired by it (not used at runtime); stitching/vstack handled directly via FFmpeg filter_complex",
        "py_module": "moviepy",
        "cli": None,
    },
    {
        "id": "18",
        "name": "Remotion",
        "stage": "04 / CAPTION",
        "stage_key": "caption",
        "github": "https://github.com/remotion-dev/remotion",
        "role": "Animated captions and overlays in React",
        "integration": "executed",
        "engine_note": "npx remotion render (@remotion/cli 4.0.545) invoked via 18 REMOTION RENDER button",
        "py_module": None,
        "cli": "remotion",
    },
    {
        "id": "19",
        "name": "LosslessCut",
        "stage": "05 / SHIP",
        "stage_key": "ship",
        "github": "https://github.com/mifi/lossless-cut",
        "role": "Trim without re-encoding",
        "integration": "equivalent",
        "engine_note": "Inspired by it (re-implemented in-house); uses identical FFmpeg stream-copy (-ss -to -c copy) lossless trim",
        "py_module": None,
        "cli": None,
    },
    {
        "id": "20",
        "name": "FFmpeg",
        "stage": "05 / SHIP",
        "stage_key": "ship",
        "github": "https://github.com/FFmpeg/FFmpeg",
        "role": "Encode the final 9:16 export",
        "integration": "executed",
        "engine_note": "FFmpeg (libx264, libass, loudnorm -14 LUFS, 48kHz AAC) encodes all 9:16 exports",
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
    """TWITCHDOWNLOADER_CLI (.env) > bin/TwitchDownloaderCLI > PATH. None when not installed."""
    if TWITCHDOWNLOADER_CLI and Path(TWITCHDOWNLOADER_CLI).exists():
        return TWITCHDOWNLOADER_CLI
    local = BIN_DIR / _exe("TwitchDownloaderCLI")
    if local.exists():
        return str(local)
    return shutil.which("TwitchDownloaderCLI")


def get_yolo_weights_path() -> Path:
    return MODELS_DIR / YOLO_WEIGHTS_NAME


def get_face_model_path() -> Path:
    return MODELS_DIR / FACE_MODEL_NAME


def ensure_face_model() -> Path | None:
    """Download the MediaPipe BlazeFace model on first use (Apache-2.0). Returns None if unavailable."""
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
                # "ready" = the tool itself runs in this pipeline. Equivalent engines are never counted.
                "ready": bool(executed and runtime_ready),
                "status": (
                    "executed" if executed and runtime_ready
                    else "executed_missing" if executed
                    else "equivalent"
                ),
            }
        )
    return results
