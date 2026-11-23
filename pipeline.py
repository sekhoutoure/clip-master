from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

import tracked_render
from config import BASE_DIR, BIN_DIR, DOWNLOAD_TIMEOUT, MODELS_DIR, OUTPUTS_DIR, PROJECTS_DIR
from tools_registry import ensure_face_model, get_ffmpeg_path, get_twitch_cli, get_yolo_weights_path

REMOTION_DIR = BASE_DIR / "remotion"

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)

_PROJECT_LOCKS: dict[str, threading.RLock] = {}
_PROJECT_RUN_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_REMOTION_LOCK = threading.Lock()
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,47}$")
_WHISPER_CACHE: dict[str, Any] = {}
_YOLO_CACHE: dict[str, Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def validate_project_id(project_id: str) -> Path:
    """Validate project_id format and guarantee resolved path stays strictly inside PROJECTS_DIR."""
    if not isinstance(project_id, str) or not _PROJECT_ID_RE.match(project_id):
        raise ValueError(f"Identifiant de projet invalide: {project_id!r}")
    pdir = (PROJECTS_DIR / project_id).resolve()
    if not pdir.is_relative_to(PROJECTS_DIR.resolve()) or pdir == PROJECTS_DIR.resolve():
        raise ValueError(f"Chemin de projet hors confinement: {project_id!r}")
    return pdir


def _project_lock(project_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        if project_id not in _PROJECT_LOCKS:
            _PROJECT_LOCKS[project_id] = threading.RLock()
        return _PROJECT_LOCKS[project_id]


def project_run_lock(project_id: str) -> threading.Lock:
    """Held for the duration of a mutating job; does not block project JSON reads."""
    with _LOCKS_GUARD:
        if project_id not in _PROJECT_RUN_LOCKS:
            _PROJECT_RUN_LOCKS[project_id] = threading.Lock()
        return _PROJECT_RUN_LOCKS[project_id]


def _popen_kwargs() -> dict[str, Any]:
    kw: dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return kw

ProgressCallback = Callable[[str, int, str, list[str]], None]


def _venv_exe(name: str) -> str:
    """Locate a CLI installed next to the running Python (venv Scripts/bin), else on PATH."""
    scripts_dir = Path(sys.executable).parent
    for cand in (scripts_dir / f"{name}.exe", scripts_dir / name):
        if cand.exists():
            return str(cand)
    return shutil.which(name) or name


_URL_RE = re.compile(r"^https?://[^\s\x00-\x1f\"'<>`|;&$\\]+$", re.I)
_TIME_RE = re.compile(r"^\d{1,5}(?::\d{1,2}){0,2}(?:\.\d{1,3})?$")


def validate_source_url(url: str | None) -> str:
    """Only plain http(s) URLs are accepted: no option-looking values, no whitespace or shell characters."""
    u = (url or "").strip()
    if not u or len(u) > 2048 or not _URL_RE.match(u):
        raise ValueError("URL invalide : utilise un lien http(s) complet, sans espaces.")
    return u


def validate_time_spec(value: str | None) -> str | None:
    """Accept SS, MM:SS or HH:MM:SS (optionally with .mmm); reject anything else."""
    if value is None or str(value).strip() == "":
        return None
    v = str(value).strip()
    if not _TIME_RE.match(v):
        raise ValueError(f"Horodatage invalide : {value!r} (attendu SS, MM:SS ou HH:MM:SS).")
    return v


def _run_cmd(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = str(BIN_DIR) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        **_popen_kwargs(),
    )


def get_whisper_model(model_size: str, cpu_threads: int | None = None):
    """Reuse faster-whisper models across jobs (expensive to load on CPU)."""
    from faster_whisper import WhisperModel

    key = f"{model_size}:{cpu_threads or 0}"
    with _MODEL_CACHE_LOCK:
        if key not in _WHISPER_CACHE:
            kw: dict[str, Any] = {"device": "cpu", "compute_type": "int8"}
            if cpu_threads:
                kw["cpu_threads"] = cpu_threads
            _WHISPER_CACHE[key] = WhisperModel(model_size, **kw)
        return _WHISPER_CACHE[key]


def get_yolo_model(weights: Path | None = None):
    """Reuse the YOLOv8n weights across track/render jobs."""
    path = Path(weights) if weights is not None else get_yolo_weights_path()
    if not path.exists():
        # First use: let Ultralytics download the official yolov8n weights into models/
        try:
            from ultralytics.utils.downloads import attempt_download_asset

            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            attempt_download_asset(str(path))
        except Exception:
            pass
    key = str(path.resolve()) if path.exists() else ""
    if not key:
        return None
    with _MODEL_CACHE_LOCK:
        if key not in _YOLO_CACHE:
            from ultralytics import YOLO
            _YOLO_CACHE[key] = YOLO(key)
        return _YOLO_CACHE[key]


def get_video_info(video_path: Path) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Impossible d'ouvrir la vidéo: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    cap.release()
    duration = round(frame_count / fps, 2) if fps > 0 and frame_count > 0 else 0.0
    return {
        "width": width,
        "height": height,
        "fps": round(fps, 2),
        "frame_count": frame_count,
        "duration": duration,
    }


def extract_filmstrip(video_path: Path, out_dir: Path, count: int = 12) -> list[str]:
    thumbs_dir = out_dir / "filmstrip"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    paths: list[str] = []
    if total <= 0:
        cap.release()
        return paths
    for i in range(count):
        idx = int((i + 0.5) * total / count)
        idx = max(0, min(total - 1, idx))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok:
            h, w = frame.shape[:2]
            tw = 160
            th = max(60, int(h * (tw / w)))
            resized = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
            fname = f"strip_{i:02d}.jpg"
            cv2.imwrite(str(thumbs_dir / fname), resized, [cv2.IMWRITE_JPEG_QUALITY, 80])
            paths.append(fname)
    cap.release()
    return paths


def extract_waveform(video_path: Path, bars: int = 120) -> list[float]:
    """Stream-decode mono 8kHz PCM in bounded 64KB chunks (never buffers full VOD PCM in RAM)."""
    ffmpeg = get_ffmpeg_path()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "s16le",
        "-",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_popen_kwargs())
        chunk_means: list[float] = []
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(65536)
            if not buf:
                break
            usable = len(buf) - (len(buf) % 2)
            if usable > 0:
                arr = np.frombuffer(buf[:usable], dtype=np.int16).astype(np.float32)
                # Sub-split each 64KB chunk (~4s) into 8 mini-bins for resolution
                for sub in np.array_split(np.abs(arr), 8):
                    if sub.size:
                        chunk_means.append(float(np.mean(sub)))
        proc.wait(timeout=60)
        if proc.returncode != 0 or not chunk_means:
            return [round(0.2 + 0.6 * abs(math.sin(i * 0.35)), 3) for i in range(bars)]
        arr_m = np.array(chunk_means, dtype=np.float32)
        if arr_m.size < bars:
            peaks = np.interp(np.linspace(0, arr_m.size - 1, bars), np.arange(arr_m.size), arr_m)
        else:
            peaks = np.array([float(np.mean(c)) for c in np.array_split(arr_m, bars)])
        max_p = float(np.max(peaks)) or 1.0
        norm = peaks / max_p
        return [round(max(0.06, float(v)), 3) for v in norm]
    except Exception:
        return [round(0.2 + 0.6 * abs(math.sin(i * 0.35)), 3) for i in range(bars)]


def save_project(project_id: str, data: dict[str, Any]) -> Path:
    pdir = validate_project_id(project_id)
    with _project_lock(project_id):
        pdir.mkdir(parents=True, exist_ok=True)
        meta_path = pdir / "project.json"
        tmp_path = pdir / "project.json.tmp"
        tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, meta_path)
        return meta_path


def load_project(project_id: str) -> dict[str, Any]:
    pdir = validate_project_id(project_id)
    with _project_lock(project_id):
        meta_path = pdir / "project.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Projet introuvable: {project_id}")
        return json.loads(meta_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# STAGE 01 / PULL — yt-dlp, streamlink, TwitchDownloader
# ---------------------------------------------------------------------------
def _stage_01_pull_impl(
    source_type: str,
    url: str | None = None,
    local_file: Path | None = None,
    title: str | None = None,
    clip_start: str | None = None,
    clip_end: str | None = None,
    record_seconds: int = 30,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    pdir = PROJECTS_DIR / project_id
    pdir.mkdir(parents=True, exist_ok=True)
    dest_video = pdir / "source.mp4"

    active_repos = ["01", "02", "03"]
    if progress:
        progress("01 / PULL", 10, "yt-dlp / streamlink. VOD in.", active_repos)

    source_label = title or "SOURCE_01"

    if source_type == "local" and local_file and local_file.exists():
        shutil.copy2(local_file, dest_video)
        source_label = title or local_file.stem.upper()
    elif source_type == "twitchdownloader" and url:
        td_exe = get_twitch_cli()
        m = re.search(r"(?:videos/|v/)?(\d{7,})", url)
        vod_id = m.group(1) if m else None
        if td_exe and vod_id:
            if progress:
                progress("01 / PULL", 25, f"TwitchDownloaderCLI pulling VOD {vod_id} + chat...", ["03"])
            # Download chat JSON in parallel/first
            chat_json = pdir / "twitch_chat.json"
            _run_cmd([str(td_exe), "chatdownload", "--id", vod_id, "-o", str(chat_json)], timeout=180)
            res = _run_cmd(
                [str(td_exe), "videodownload", "--id", vod_id, "-o", str(dest_video), "--ffmpeg-path", get_ffmpeg_path()],
                timeout=DOWNLOAD_TIMEOUT,
            )
            if res.returncode != 0 or not dest_video.exists():
                raise RuntimeError(f"TwitchDownloaderCLI failed: {res.stderr[-300:] or res.stdout[-300:]}")
            source_label = title or f"TWITCH_VOD_{vod_id}"
        else:
            # Fallback to yt-dlp for clips/non-VOD URLs
            if progress:
                progress("01 / PULL", 25, "TwitchDownloader fallback to yt-dlp for clip URL...", ["01", "03"])
            ytdlp = _venv_exe("yt-dlp")
            res = _run_cmd(
                [ytdlp, "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best", "--merge-output-format", "mp4", "--ffmpeg-location", get_ffmpeg_path(), "--no-playlist", "-o", str(dest_video), "--", url],
                timeout=DOWNLOAD_TIMEOUT,
            )
            if res.returncode != 0 or not dest_video.exists():
                raise RuntimeError(f"Twitch pull failed: {res.stderr[-300:]}")
            source_label = title or "TWITCH_CLIP"
    elif source_type == "streamlink" and url:
        if progress:
            progress("01 / PULL", 25, f"streamlink recording {record_seconds}s live stream...", ["02"])
        sl = _venv_exe("streamlink")
        ts_file = pdir / "live.ts"
        cmd = [sl, "-o", str(ts_file), "--", url, "best"]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_popen_kwargs())
        try:
            time.sleep(max(5, min(record_seconds, 120)))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if not ts_file.exists() or ts_file.stat().st_size < 1000:
            raise RuntimeError("streamlink n'a pas pu enregistrer le stream (vérifie que la chaîne est en live).")
        ffmpeg = get_ffmpeg_path()
        remux = _run_cmd([ffmpeg, "-y", "-i", str(ts_file), "-c", "copy", str(dest_video)])
        ts_file.unlink(missing_ok=True)
        if remux.returncode != 0 or not dest_video.exists():
            raise RuntimeError(f"Remux FFmpeg du stream échoué: {remux.stderr[-300:]}")
        source_label = "LIVE_STREAM"
    elif url:
        # Avoid downloading multi-hour Twitch VODs whole when no section is given:
        # extract ?t=00h00m01s (or ?t=123) and pull a clean 90s clip window instead.
        eff_start, eff_end = clip_start, clip_end
        is_long_vod = bool(re.search(r"twitch\.tv/videos/", url, re.I))
        if is_long_vod and not (eff_start and eff_end):
            t_sec = 0
            m_hms = re.search(r"[?&]t=(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", url, re.I)
            if m_hms and any(m_hms.groups()):
                if m_hms.group(1) or m_hms.group(2) or "s" in m_hms.group(0).lower():
                    t_sec = int(m_hms.group(1) or 0) * 3600 + int(m_hms.group(2) or 0) * 60 + int(m_hms.group(3) or 0)
                else:
                    t_sec = int(m_hms.group(3) or 0)
            def _hms(s: int) -> str:
                return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
            eff_start = _hms(t_sec)
            eff_end = _hms(t_sec + 90)
        if progress:
            msg = f"yt-dlp : extrait {eff_start} → {eff_end}..." if (eff_start and eff_end) else "yt-dlp downloading VOD / clip..."
            progress("01 / PULL", 25, msg, ["01"])
        ytdlp = _venv_exe("yt-dlp")
        cmd = [
            ytdlp,
            "-f",
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
            "--merge-output-format",
            "mp4",
            "--ffmpeg-location",
            get_ffmpeg_path(),
            "--no-playlist",
            "-o",
            str(dest_video),
        ]
        if eff_start and eff_end:
            cmd += ["--download-sections", f"*{eff_start}-{eff_end}"]
        cmd += ["--", url]
        res = _run_cmd(cmd, timeout=DOWNLOAD_TIMEOUT)
        if res.returncode != 0 or not dest_video.exists():
            raise RuntimeError(f"yt-dlp a échoué: {res.stderr[-400:]}")
        source_label = title or "VOD_CLIP"
    else:
        raise ValueError("Fournis une URL ou un fichier vidéo local.")

    if progress:
        progress("01 / PULL", 70, "Extracting filmstrip & audio waveform...", active_repos)

    info = get_video_info(dest_video)
    filmstrip = extract_filmstrip(dest_video, pdir, count=12)
    waveform = extract_waveform(dest_video, bars=120)

    project = {
        "id": project_id,
        "name": source_label,
        "source_url": url or "",
        "video_file": "source.mp4",
        "info": info,
        "filmstrip": filmstrip,
        "waveform": waveform,
        "stage_completed": ["pull"],
        "cuts": [],
        "moments": [],
        "highlight": {"start": 0.0, "end": min(info["duration"], 20.0), "score": None},
        "face_box": {"x": 0.02, "y": 0.16, "w": 0.19, "h": 0.25, "conf": None, "detected": False},
        "crop_x": 0.50,
        "face_track": [],
        "captions": [],
        "font_size": 40,
        "layout_mode": "FACECAM + GAME + CAPS",
        "exports": [],
    }
    save_project(project_id, project)
    if progress:
        progress("01 / PULL", 100, f"Source ready ({info['duration']}s, {info['width']}x{info['height']})", active_repos)
    return project


def stage_01_pull(
    source_type: str,
    url: str | None = None,
    local_file: Path | None = None,
    title: str | None = None,
    clip_start: str | None = None,
    clip_end: str | None = None,
    record_seconds: int = 30,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Validate the inputs, then import a source. A failed import leaves no half-built project behind."""
    if source_type not in ("ytdlp", "streamlink", "twitchdownloader", "local"):
        raise ValueError(f"Type de source inconnu : {source_type!r}")
    if source_type != "local":
        url = validate_source_url(url)
    clip_start, clip_end = validate_time_spec(clip_start), validate_time_spec(clip_end)
    title = (title or "").strip()[:120] or None
    record_seconds = max(5, min(int(record_seconds or 30), 120))
    before = {p.name for p in PROJECTS_DIR.iterdir()} if PROJECTS_DIR.exists() else set()
    try:
        return _stage_01_pull_impl(
            source_type, url=url, local_file=local_file, title=title, clip_start=clip_start,
            clip_end=clip_end, record_seconds=record_seconds, progress=progress,
        )
    except BaseException:
        for p in PROJECTS_DIR.iterdir():
            if p.name not in before and p.is_dir() and not (p / "project.json").exists():
                shutil.rmtree(p, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# STAGE 02 / FIND — PySceneDetect, auto-editor, FunClip, ClipsAI
# ---------------------------------------------------------------------------
def stage_02_find(
    project_id: str,
    strip_silence: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    project = load_project(project_id)
    pdir = PROJECTS_DIR / project_id
    video_path = pdir / project["video_file"]
    active_repos = ["04", "05", "06", "07"]

    # Optional auto-editor dead air removal FIRST so cuts & scoring match the final trimmed video
    if strip_silence:
        if progress:
            progress("02 / FIND", 20, "auto-editor stripping dead air & silence...", ["05"])
        ae = _venv_exe("auto-editor")
        cleaned = pdir / "source_clean.mp4"
        res = _run_cmd([ae, str(video_path), "--margin", "0.2sec", "-o", str(cleaned)], timeout=240)
        if res.returncode == 0 and cleaned.exists() and cleaned.stat().st_size > 10_000:
            project["video_file"] = "source_clean.mp4"
            video_path = cleaned
            project["info"] = get_video_info(video_path)
            project["filmstrip"] = extract_filmstrip(video_path, pdir, count=12)
            project["waveform"] = extract_waveform(video_path, bars=120)

    if progress:
        progress("02 / FIND", 45, "PySceneDetect scanning scene cuts...", ["04"])

    cuts: list[dict[str, float]] = []
    try:
        from scenedetect import ContentDetector, detect

        scene_list = detect(str(video_path), ContentDetector(threshold=27.0))
        for start_tc, end_tc in scene_list:
            cuts.append({"start": round(start_tc.get_seconds(), 2), "end": round(end_tc.get_seconds(), 2)})
    except Exception:
        dur = project["info"]["duration"]
        step = max(2.5, dur / 6.0)
        t = 0.0
        while t < dur:
            cuts.append({"start": round(t, 2), "end": round(min(dur, t + step), 2)})
            t += step

    if not cuts:
        dur = project["info"]["duration"]
        step = max(2.5, dur / 5.0)
        t = 0.0
        while t < dur:
            cuts.append({"start": round(t, 2), "end": round(min(dur, t + step), 2)})
            t += step

    if progress:
        progress("02 / FIND", 70, "Scoring moments (audio energy + scene-cut density)...", ["04"])

    # Real scoring: sliding window over audio RMS waveform + scene-cut density.
    # score = 0.7 * normalized audio energy + 0.3 * normalized cut density (0..1, no fake offset)
    waveform = project.get("waveform") or [0.5] * 120
    dur = float(project["info"]["duration"] or 1.0)
    moments_dir = pdir / "moments"
    moments_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    fps = float(project["info"]["fps"] or 30.0)
    labels = ["MOMENT_01", "MOMENT_02", "MOMENT_03"]
    moments = []

    win = float(min(15.0, max(4.0, dur * 0.25)))
    step = max(1.0, win / 3.0)
    cut_starts = np.array([c["start"] for c in cuts], dtype=np.float32) if cuts else np.array([], dtype=np.float32)
    candidates = []
    t0 = 0.0
    while t0 + win <= dur + 0.01 or not candidates:
        t1 = min(dur, t0 + win)
        b0 = int((t0 / dur) * len(waveform))
        b1 = max(b0 + 1, int((t1 / dur) * len(waveform)))
        energy = float(np.mean(waveform[b0:b1]))
        density = float(np.sum((cut_starts >= t0) & (cut_starts < t1))) if cut_starts.size else 0.0
        candidates.append([t0, t1, energy, density])
        if t1 >= dur:
            break
        t0 += step
    cand = np.array(candidates, dtype=np.float32)
    e_norm = cand[:, 2] / (cand[:, 2].max() or 1.0)
    d_norm = cand[:, 3] / (cand[:, 3].max() or 1.0)
    scores = 0.7 * e_norm + 0.3 * d_norm

    # Greedy non-overlapping top-3 windows
    picked: list[int] = []
    for i in np.argsort(-scores):
        s_i, e_i = cand[i, 0], cand[i, 1]
        if all(e_i <= cand[j, 0] or s_i >= cand[j, 1] for j in picked):
            picked.append(int(i))
        if len(picked) == 3:
            break
    picked.sort(key=lambda i: cand[i, 0])

    best_score = -1.0
    best_window = {"start": 0.0, "end": min(dur, win), "score": 0.0}
    for idx, ci in enumerate(picked):
        s_t = round(float(cand[ci, 0]), 2)
        e_t = round(float(cand[ci, 1]), 2)
        score = round(float(scores[ci]), 2)
        mid_t = (s_t + e_t) / 2.0

        cap.set(cv2.CAP_PROP_POS_FRAMES, int(mid_t * fps))
        ok, frame = cap.read()
        thumb_name = f"moment_{idx:02d}.jpg"
        if ok:
            h, w = frame.shape[:2]
            tw = 240
            th = max(90, int(h * (tw / w)))
            cv2.imwrite(str(moments_dir / thumb_name), cv2.resize(frame, (tw, th)), [cv2.IMWRITE_JPEG_QUALITY, 85])

        m = {
            "id": f"m_{idx+1}",
            "label": labels[idx],
            "start": s_t,
            "end": e_t,
            "score": score,
            "thumb": f"moments/{thumb_name}",
        }
        moments.append(m)
        if score > best_score:
            best_score = score
            best_window = {"start": s_t, "end": e_t, "score": score}

    cap.release()

    project["cuts"] = cuts
    project["moments"] = moments
    project["highlight"] = best_window
    if "find" not in project["stage_completed"]:
        project["stage_completed"].append("find")
    save_project(project_id, project)

    if progress:
        progress("02 / FIND", 100, f"{len(cuts)} cuts found. Dead air gone.", active_repos)
    return project


# ---------------------------------------------------------------------------
# STAGE 03 / TRACK — MediaPipe (08) + Ultralytics (09)
# ---------------------------------------------------------------------------
def stage_03_track(
    project_id: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Real tracking:
    - 08 MediaPipe FaceDetector (BlazeFace) -> facecam box + real detection confidence
    - 09 Ultralytics YOLOv8n -> gameplay person subject X + real box confidence
    - Fallback when no detector output: OpenCV edge-energy centroid (flagged as 'saliency').
    No hardcoded confidence values: conf is None when nothing was detected.
    """
    project = load_project(project_id)
    pdir = PROJECTS_DIR / project_id
    video_path = pdir / project["video_file"]
    active_repos = ["08", "09"]

    if progress:
        progress("03 / TRACK", 10, "08 MediaPipe loading BlazeFace detector...", ["08"])

    cap = cv2.VideoCapture(str(video_path))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
    dur = float(project["info"]["duration"] or (total / fps))

    face_detector = None
    face_engine = "none"
    model_path = ensure_face_model()
    try:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        if model_path is not None:
            face_detector = vision.FaceDetector.create_from_options(
                vision.FaceDetectorOptions(
                    base_options=BaseOptions(model_asset_path=str(model_path)),
                    min_detection_confidence=0.45,
                )
            )
            face_engine = "mediapipe"
    except Exception:
        face_detector = None

    try:
        yolo_model = get_yolo_model()
    except Exception:
        yolo_model = None

    if progress:
        progress("03 / TRACK", 30, f"Sampling frames (face={face_engine}, subject={'yolo' if yolo_model else 'saliency'})...", active_repos)

    sample_count = 16
    corner_faces: list[tuple[float, float, float, float, float]] = []
    track_points = []

    for i in range(sample_count):
        t = round((i / max(1, sample_count - 1)) * dur, 2)
        f_idx = min(total - 1, int(t * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ok, frame = cap.read()
        if not ok:
            continue

        # --- 08 MediaPipe: facecam = face outside the central gameplay column
        if face_detector is not None:
            try:
                rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                res = face_detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
                for d in res.detections:
                    bb = d.bounding_box
                    cx_n = (bb.origin_x + bb.width / 2) / w
                    if cx_n < 0.34 or cx_n > 0.66:
                        corner_faces.append(
                            (bb.origin_x / w, bb.origin_y / h, bb.width / w, bb.height / h, float(d.categories[0].score))
                        )
            except Exception:
                pass

        # --- 09 Ultralytics: gameplay subject center
        center_x = None
        conf = None
        source = "saliency"
        if yolo_model is not None:
            try:
                res = yolo_model.predict(frame, classes=[0], verbose=False, imgsz=416)
                if res and len(res[0].boxes) > 0:
                    boxes = res[0].boxes.xyxyn.cpu().numpy()
                    confs = res[0].boxes.conf.cpu().numpy()
                    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
                    k = int(np.argmax(areas * confs))
                    center_x = float((boxes[k, 0] + boxes[k, 2]) / 2.0)
                    conf = round(float(confs[k]), 3)
                    source = "yolo"
            except Exception:
                pass
        if center_x is None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (160, 90))
            edges = cv2.Canny(small, 60, 150)
            col_energy = edges[10:80, 25:135].sum(axis=0).astype(np.float32)
            center_x = 0.5
            if col_energy.sum() > 0:
                xs = np.linspace(0.30, 0.70, len(col_energy))
                center_x = float(np.sum(xs * col_energy) / np.sum(col_energy))

        center_x = round(max(0.22, min(0.78, center_x)), 3)
        track_points.append({"t": t, "x": center_x, "conf": conf, "source": source})

    cap.release()
    if face_detector is not None:
        try:
            face_detector.close()
        except Exception:
            pass

    if progress:
        progress("03 / TRACK", 85, "Smoothing crop curve...", active_repos)

    # Light temporal smoothing of crop X (moving average, window 3)
    if len(track_points) >= 3:
        xs = np.array([p["x"] for p in track_points], dtype=np.float32)
        sm = np.convolve(np.pad(xs, 1, mode="edge"), np.ones(3) / 3.0, mode="valid")
        for p, v in zip(track_points, sm):
            p["x"] = round(float(v), 3)

    if corner_faces:
        arr = np.array(corner_faces, dtype=np.float32)
        med = np.median(arr[:, :4], axis=0)
        pad_w = max(0.16, float(med[2]) * 2.2)
        pad_h = max(0.20, float(med[3]) * 2.2)
        fx = max(0.0, float(med[0]) - (pad_w - float(med[2])) / 2)
        fy = max(0.0, float(med[1]) - (pad_h - float(med[3])) / 2)
        pw = min(0.40, pad_w)
        ph = min(0.45, pad_h)
        face_box = {
            "x": round(min(fx, 1 - pw), 3),
            "y": round(min(fy, 1 - ph), 3),
            "w": round(pw, 3),
            "h": round(ph, 3),
            "conf": round(float(np.median(arr[:, 4])), 3),
            "hit_rate": round(len(corner_faces) / sample_count, 2),
            "detected": True,
            "engine": face_engine,
        }
    else:
        prev = project.get("face_box") or {}
        face_box = {
            "x": prev.get("x", 0.02),
            "y": prev.get("y", 0.16),
            "w": prev.get("w", 0.20),
            "h": prev.get("h", 0.26),
            "conf": None,
            "hit_rate": 0.0,
            "detected": False,
            "engine": face_engine,
        }

    mean_crop_x = round(float(np.mean([p["x"] for p in track_points])) if track_points else 0.50, 3)
    yolo_hits = sum(1 for p in track_points if p["source"] == "yolo")

    project["face_box"] = face_box
    project["face_track"] = track_points
    project["crop_x"] = mean_crop_x
    if "track" not in project["stage_completed"]:
        project["stage_completed"].append("track")
    save_project(project_id, project)

    face_msg = (
        f"Facecam found ({face_engine} conf {face_box['conf']}, {int(face_box['hit_rate']*100)}% frames)"
        if face_box["detected"]
        else "No facecam detected (box editable manually)"
    )
    if progress:
        progress(
            "03 / TRACK",
            100,
            f"{face_msg}. Subject YOLO {yolo_hits}/{len(track_points)} frames. Crop X {mean_crop_x*100:.1f}%",
            active_repos,
        )
    return project



# ---------------------------------------------------------------------------
# STAGE 04 / CAPTION — WhisperX, faster-whisper, auto-subtitle, Remotion
# ---------------------------------------------------------------------------
def stage_04_caption(
    project_id: str,
    model_size: str = "tiny",
    language: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    project = load_project(project_id)
    pdir = PROJECTS_DIR / project_id
    video_path = pdir / project["video_file"]
    active_repos = ["11", "12", "13", "18"]

    if progress:
        progress("04 / CAPTION", 15, "Extracting audio for faster-whisper / WhisperX...", ["11", "12"])

    wav_path = pdir / "audio.wav"
    ffmpeg = get_ffmpeg_path()
    ext_res = _run_cmd([ffmpeg, "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", str(wav_path)])
    if ext_res.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size < 44:
        wav_path.unlink(missing_ok=True)
        raise RuntimeError(f"Extraction audio FFmpeg échouée (pas de piste audio ou erreur): {ext_res.stderr[-250:]}")

    captions: list[dict[str, Any]] = []
    colors = ["slate", "indigo", "teal", "orange", "pink"]

    if progress:
        progress("04 / CAPTION", 40, f"faster-whisper ({model_size}) word-level timestamps...", ["12"])
    try:
        model = get_whisper_model(model_size)
        kwargs: dict[str, Any] = {"word_timestamps": True, "vad_filter": True}
        if language and language != "auto":
            kwargs["language"] = language
        segments, _info = model.transcribe(str(wav_path), **kwargs)

        all_words = []
        for seg in segments:
            if seg.words:
                for w_obj in seg.words:
                    txt = (w_obj.word or "").strip()
                    if txt:
                        all_words.append(
                            {
                                "word": txt,
                                "start": round(float(w_obj.start), 2),
                                "end": round(float(w_obj.end), 2),
                            }
                        )
            else:
                txt = (seg.text or "").strip()
                if txt:
                    captions.append(
                        {
                            "id": f"cap_{len(captions)+1}",
                            "text": txt.upper(),
                            "start": round(float(seg.start), 2),
                            "end": round(float(seg.end), 2),
                            "color": colors[len(captions) % len(colors)],
                        }
                    )

        # Group words into punchy 3-word blocks
        if all_words:
            chunk_size = 3
            for i in range(0, len(all_words), chunk_size):
                chunk = all_words[i : i + chunk_size]
                text = " ".join(w["word"] for w in chunk).upper()
                captions.append(
                    {
                        "id": f"cap_{len(captions)+1}",
                        "text": text,
                        "start": chunk[0]["start"],
                        "end": max(chunk[-1]["end"], chunk[0]["start"] + 0.35),
                        "color": colors[len(captions) % len(colors)],
                        "words": chunk,
                    }
                )
    except Exception as exc:
        wav_path.unlink(missing_ok=True)
        raise RuntimeError(f"Transcription faster-whisper échouée: {exc}") from exc

    if progress:
        progress("04 / CAPTION", 88, "Syncing Remotion React captions & SRT...", ["13", "18"])

    # Write SRT file (auto-subtitle compatible)
    srt_lines = []
    for idx, c in enumerate(captions, 1):
        srt_lines.append(f"{idx}\n{_fmt_srt(c['start'])} --> {_fmt_srt(c['end'])}\n{c['text']}\n")
    (pdir / "captions.srt").write_text("\n".join(srt_lines), encoding="utf-8")

    # Sync Remotion captions.json under _REMOTION_LOCK
    with _REMOTION_LOCK:
        remotion_src = REMOTION_DIR / "src"
        remotion_src.mkdir(parents=True, exist_ok=True)
        (remotion_src / "captions.json").write_text(
            json.dumps({"project_id": project_id, "fps": 30, "captions": captions}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # Clean up intermediate WAV after transcription
    if wav_path.exists():
        wav_path.unlink(missing_ok=True)

    project["captions"] = captions
    if "caption" not in project["stage_completed"]:
        project["stage_completed"].append("caption")
    save_project(project_id, project)

    if progress:
        msg = f"Words timed. {len(captions)} captions ready." if captions else "No speech detected (0 captions)."
        progress("04 / CAPTION", 100, msg, active_repos)
    return project


def _fmt_srt(seconds: float) -> str:
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_ass(seconds: float) -> str:
    cs = int(round((seconds - int(seconds)) * 100))
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


CAPTION_PRESETS = {
    "pink": {"primary": "&H006C1BF0", "outline": "&H00FFFFFF", "back": "&H80000000", "italic": 0, "border_style": 3, "outline_w": 10, "shadow_w": 0},
    "yellow": {"primary": "&H0000E5FF", "outline": "&H00000000", "back": "&H80000000", "italic": 0, "border_style": 3, "outline_w": 10, "shadow_w": 0},
    "cyan": {"primary": "&H00FFD800", "outline": "&H00111111", "back": "&H80000000", "italic": 0, "border_style": 3, "outline_w": 10, "shadow_w": 0},
    "white": {"primary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H90000000", "italic": 0, "border_style": 3, "outline_w": 10, "shadow_w": 0},
    "tiktok_white": {"primary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H00000000", "italic": -1, "border_style": 1, "outline_w": 5, "shadow_w": 2},
    "tiktok_yellow": {"primary": "&H0000E5FF", "outline": "&H00000000", "back": "&H00000000", "italic": -1, "border_style": 1, "outline_w": 5, "shadow_w": 2},
}


def _write_ass_subtitles(
    captions: list[dict[str, Any]],
    out_path: Path,
    font_size: int = 40,
    split_mode: bool = True,
    caption_style: str = "pink",
) -> None:
    preset = CAPTION_PRESETS.get(caption_style, CAPTION_PRESETS["pink"])
    scaled_size = max(56, int(font_size * 1.9))
    is_tiktok = caption_style.startswith("tiktok")
    margin_v = 380 if is_tiktok else (1140 if split_mode else 420)
    italic_val = preset.get("italic", 0)
    border_style = preset.get("border_style", 3)
    outline_w = preset.get("outline_w", 10)
    shadow_w = preset.get("shadow_w", 0)
    font_name = preset.get("font", "Impact")

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Viral,{font_name},{scaled_size},{preset['primary']},&H00FFFFFF,{preset['outline']},{preset['back']},-1,{italic_val},0,0,100,100,1,0,{border_style},{outline_w},{shadow_w},2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for c in captions:
        txt = (c.get("text") or "").replace("\n", "\\N").replace("{", "").replace("}", "")
        if not txt.strip():
            continue
        lines.append(f"Dialogue: 0,{_fmt_ass(c['start'])},{_fmt_ass(c['end'])},Viral,,0,0,0,,{txt}")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def generate_viral_kit(project: dict[str, Any]) -> dict[str, Any]:
    name = (project.get("name") or "CLIP").replace("_", " ").title()
    caps = [c.get("text", "").strip() for c in project.get("captions", []) if c.get("text", "").strip()]
    hook_line = caps[0] if caps else "YOU WON'T BELIEVE THIS MOMENT"
    snippet = " ".join(caps[:4])[:140] if caps else f"Insane moment from {name} — clipped & reframed in 9:16."
    title = f"{hook_line.title()} | {name} #Shorts"
    hashtags = "#Shorts #GamingClips #StreamClips #ViralShorts #ClipMaster"
    return {
        "title": title[:100],
        "hook": hook_line,
        "description": f"{snippet}\n\n{hashtags}",
        "hashtags": hashtags,
    }


# ---------------------------------------------------------------------------
# STAGE 05 / SHIP — Demucs (14), LosslessCut-style copy trim (19), FFmpeg (20)
# Slow-mo uses FFmpeg minterpolate (motion-compensated interpolation) + atempo.
# Sharpen uses FFmpeg unsharp. (Video2X / Practical-RIFE / MoviePy are cloned but not executed.)
# ---------------------------------------------------------------------------
def _atempo_chain(speed: float) -> str:
    """atempo only accepts 0.5..2.0 per instance -> chain for wider ranges."""
    speed = max(0.25, min(2.0, speed))
    parts = []
    while speed < 0.5:
        parts.append("atempo=0.5")
        speed /= 0.5
    parts.append(f"atempo={speed:.4f}")
    return ",".join(parts)


def _ffmpeg_path_escape(p: Path) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _build_vertical_filter(
    project: dict[str, Any],
    cx: float,
    split_mode: bool,
    speed: float = 1.0,
    ass_path: Path | None = None,
    retention_dur: float | None = None,
    sharpen: bool = False,
    interpolate: bool = False,
) -> str:
    info = project["info"]
    src_w = int(info["width"])
    src_h = int(info["height"])

    pre = ""
    if abs(speed - 1.0) > 0.05:
        pre = f"setpts={1.0 / speed:.4f}*PTS,"

    post: list[str] = []
    if ass_path is not None:
        post.append(f"ass='{_ffmpeg_path_escape(ass_path)}'")
    if retention_dur:
        post.append(f"drawbox=x=0:y=1904:w='min(1080,1080*t/{retention_dur:.3f})':h=16:color=#ee1d6c@0.95:t=fill")
    if sharpen:
        post.append("unsharp=5:5:0.8:3:3:0.4")
    if interpolate and speed < 0.95:
        post.append("minterpolate=fps=30:mi_mode=blend")
    post_chain = ("," + ",".join(post)) if post else ""

    if split_mode:
        fb = project.get("face_box") or {}
        fw = max(64, int(float(fb.get("w", 0.20)) * src_w) & ~1)
        fh = max(64, int(float(fb.get("h", 0.26)) * src_h) & ~1)
        fx = max(0, min(src_w - fw, int(float(fb.get("x", 0.02)) * src_w)))
        fy = max(0, min(src_h - fh, int(float(fb.get("y", 0.16)) * src_h)))
        gw = min(src_w, max(64, int(src_h * (1080 / 1200)) & ~1))
        gx = max(0, min(src_w - gw, int(cx * src_w - gw / 2)))
        return (
            f"[0:v]{pre}split=2[vface][vgame];"
            f"[vface]crop={fw}:{fh}:{fx}:{fy},scale=1080:720:force_original_aspect_ratio=increase,crop=1080:720,setsar=1[top];"
            f"[vgame]crop={gw}:{src_h}:{gx}:0,scale=1080:1200:force_original_aspect_ratio=increase,crop=1080:1200,setsar=1[bot];"
            f"[top][bot]vstack=inputs=2,fps=30,format=yuv420p{post_chain}[outv]"
        )
    gw = min(src_w, max(64, int(src_h * (9 / 16)) & ~1))
    gx = max(0, min(src_w - gw, int(cx * src_w - gw / 2)))
    return f"[0:v]{pre}crop={gw}:{src_h}:{gx}:0,scale=1080:1920:flags=lanczos,setsar=1,fps=30,format=yuv420p{post_chain}[outv]"


def _render_vertical(
    project: dict[str, Any],
    src_video: Path,
    out_path: Path,
    s_t: float,
    e_t: float,
    cx: float,
    split_mode: bool,
    speed: float = 1.0,
    ass_path: Path | None = None,
    retention: bool = False,
    sharpen: bool = False,
    interpolate: bool = False,
    normalize_audio: bool = True,
    audio_override: Path | None = None,
    seek: bool = True,
) -> subprocess.CompletedProcess:
    ffmpeg = get_ffmpeg_path()
    out_dur = max(0.5, (e_t - s_t) / speed)
    vf = _build_vertical_filter(
        project, cx, split_mode, speed=speed, ass_path=ass_path,
        retention_dur=out_dur if retention else None, sharpen=sharpen, interpolate=interpolate,
    )
    cmd = [ffmpeg, "-y", "-hide_banner"]
    if seek:
        cmd += ["-ss", f"{s_t:.3f}", "-to", f"{e_t:.3f}"]
    cmd += ["-i", str(src_video)]
    if audio_override:
        if seek:
            cmd += ["-ss", f"{s_t:.3f}", "-to", f"{e_t:.3f}"]
        cmd += ["-i", str(audio_override)]
    cmd += ["-filter_complex", vf, "-map", "[outv]"]
    cmd += ["-map", "1:a:0" if audio_override else "0:a:0?"]

    afilters = []
    if abs(speed - 1.0) > 0.05:
        afilters.append(_atempo_chain(speed))
    if normalize_audio:
        afilters.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    if afilters:
        cmd += ["-af", ",".join(afilters)]
    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(out_path),
    ]
    return _run_cmd(cmd, timeout=900)


def stage_05_ship(
    project_id: str,
    layout_mode: str = "FACECAM + GAME + CAPS",
    crop_x: float | None = None,
    font_size: int = 40,
    caption_style: str = "pink",
    use_demucs: bool = False,
    lossless_trim: bool = False,
    playback_speed: float = 1.0,
    upscale_sharpen: bool = False,
    retention_bar: bool = True,
    normalize_audio: bool = True,
    interpolate_slowmo: bool = True,
    start_sec: float | None = None,
    end_sec: float | None = None,
    short_label: str = "SHORT_01",
    tracked: dict[str, Any] | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    project = load_project(project_id)
    pdir = PROJECTS_DIR / project_id
    video_path = pdir / project["video_file"]
    ffmpeg = get_ffmpeg_path()
    active_repos = ["14", "19", "20"]
    # Tracked mode (YOLO crop + facecam inset): explicit request options win, else the project's saved options
    t_opts = tracked_render.normalize_options(
        tracked if tracked is not None else ((project.get("tracked") or {}).get("options") or {})
    )

    if progress:
        progress("05 / SHIP", 10, "Preparing 9:16 composition...", ["20"])

    dur = float(project["info"]["duration"] or 10.0)
    cx = crop_x if crop_x is not None else float(project.get("crop_x", 0.5))
    speed = max(0.25, min(2.0, float(playback_speed or 1.0)))

    s_t = max(0.0, float(start_sec) if start_sec is not None else 0.0)
    e_t = min(dur, float(end_sec) if end_sec is not None else dur)
    if e_t - s_t < 0.3:
        s_t, e_t = 0.0, dur

    if t_opts["enabled"]:
        use_demucs = False      # tracked mode keeps the original audio
        lossless_trim = False   # frame-accurate cut needed

    temp_files: list[Path] = []

    # 19 LosslessCut-style stream-copy trim (keyframe-accurate, no re-encode)
    working_video = video_path
    seek = True
    if lossless_trim and (s_t > 0.1 or e_t < dur - 0.1):
        if progress:
            progress("05 / SHIP", 20, "Stream-copy trim (LosslessCut method, -c copy)...", ["19"])
        trimmed = pdir / "trimmed_lossless.mp4"
        res = _run_cmd([ffmpeg, "-y", "-ss", f"{s_t:.3f}", "-to", f"{e_t:.3f}", "-i", str(video_path), "-c", "copy", str(trimmed)])
        if res.returncode == 0 and trimmed.exists():
            working_video = trimmed
            temp_files.append(trimmed)
            seek = False
            # stream copy snaps to the previous keyframe: measure the real resulting duration
            real = get_video_info(trimmed)["duration"] or (e_t - s_t)
            e_t = s_t + real

    # 14 Demucs vocal isolation (runs on the working clip)
    audio_override = None
    if use_demucs:
        if progress:
            progress("05 / SHIP", 35, "14 Demucs splitting voice from game music (CPU, can be slow)...", ["14"])
        demucs_out = pdir / "demucs_out"
        res = _run_cmd(
            [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(demucs_out), str(working_video)],
            timeout=900,
        )
        vocals = list(demucs_out.rglob("vocals.wav")) if res.returncode == 0 else []
        if vocals:
            audio_override = vocals[0]
        temp_files.append(demucs_out)

    # Captions: output timeline starts at 0 and is stretched by 1/speed
    shifted_caps = []
    for c in project.get("captions", []):
        cs, ce = float(c["start"]), float(c["end"])
        if ce <= s_t or cs >= e_t:
            continue
        cs = (max(cs, s_t) - s_t) / speed
        ce = (min(ce, e_t) - s_t) / speed
        shifted_caps.append({**c, "start": cs, "end": max(cs + 0.2, ce)})

    split_mode = "FACECAM" in layout_mode.upper()
    ass_path = pdir / "render_caps.ass"
    _write_ass_subtitles(shifted_caps, ass_path, font_size=font_size, split_mode=split_mode, caption_style=caption_style)

    out_name = f"{'TRACKED_' if t_opts['enabled'] else ''}{short_label}_{project_id}_{int(time.time())}.mp4"
    out_path = OUTPUTS_DIR / out_name
    interp = interpolate_slowmo and speed < 0.95

    if t_opts["enabled"]:
        def _tp(pct: float, msg: str) -> None:
            if progress:
                progress("05 / SHIP", int(10 + pct * 0.88), msg, ["08", "09", "20"])

        report = tracked_render.render_tracked_short(
            project=project, src_video=video_path, out_path=out_path, s_t=s_t, e_t=e_t, opts=t_opts,
            captions=project.get("captions", []), speed=speed, retention=retention_bar, sharpen=upscale_sharpen,
            work_dir=pdir, progress=_tp,
        )
        out_info = get_video_info(out_path)
        project = load_project(project_id)
        project.setdefault("tracked", {})["options"] = t_opts
        kit = generate_viral_kit(project)
        qc = report.get("qc") or {}
        qc_ok = bool(qc.get("pass"))
        qc_failures = qc.get("failures") or []
        rejected = bool(t_opts["strict"] and not qc_ok)
        if rejected and out_path.exists():
            # Keep the file, but flag it so it is not treated as validated
            rejected_name = f"REJECTED_{out_name}"
            rejected_path = OUTPUTS_DIR / rejected_name
            try:
                os.replace(out_path, rejected_path)
                out_name = rejected_name
                out_path = rejected_path
                out_info = get_video_info(out_path)
            except OSError:
                pass
        export_entry = {
            "filename": out_name,
            "layout_mode": "TRACKED 9:16",
            "size_mb": round(out_path.stat().st_size / (1024 * 1024), 2) if out_path.exists() else 0,
            "duration": out_info["duration"],
            "speed": speed,
            "captions_burned": report["captions_burned"],
            "created_at": int(time.time()),
            "tracked": {
                "report": report, "clip_start": s_t, "clip_end": e_t,
                "validated": False if rejected else qc_ok, "qc_pass": qc_ok, "rejected": rejected, "kit": kit,
            },
        }
        project.setdefault("exports", []).insert(0, export_entry)
        project["crop_x"] = cx
        project["viral_kit"] = kit
        if "ship" not in project["stage_completed"]:
            project["stage_completed"].append("ship")
        save_project(project_id, project)
        if rejected:
            why = " ; ".join(qc_failures) or "QC non conforme"
            raise RuntimeError(f"QC échoué ({why}). Fichier conservé comme {out_name} (non validé).")
        if progress:
            flag = "QC OK" if qc_ok else "QC à vérifier"
            progress("05 / SHIP", 100, f"TRACKED READY — {out_name} ({qc.get('width')}x{qc.get('height')}, {qc.get('duration')}s, {qc.get('lufs_I')} LUFS, {flag})", ["20"])
        return {"project": project, "export": export_entry}

    if progress:
        msg = f"20 FFmpeg encoding 9:16 H.264 ({(e_t - s_t) / speed:.1f}s"
        msg += f", slow-mo {speed}x + minterpolate" if interp else (f", speed {speed}x" if speed != 1.0 else "")
        progress("05 / SHIP", 55, msg + ")...", ["20"])

    common = dict(
        project=project, src_video=working_video, out_path=out_path, s_t=s_t, e_t=e_t, cx=cx,
        split_mode=split_mode, speed=speed, retention=retention_bar, sharpen=upscale_sharpen,
        interpolate=interp, normalize_audio=normalize_audio, audio_override=audio_override, seek=seek,
    )
    res = _render_vertical(ass_path=ass_path if shifted_caps else None, **common)
    captions_burned = bool(shifted_caps)
    if res.returncode != 0 or not out_path.exists():
        # Retry without subtitles (libass / font issue) — filtergraph rebuilt cleanly, no regex surgery
        res = _render_vertical(ass_path=None, **common)
        captions_burned = False
        if res.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"FFmpeg export a échoué: {res.stderr[-500:]}")

    for tf in temp_files:
        try:
            if tf.is_dir():
                shutil.rmtree(tf, ignore_errors=True)
            else:
                tf.unlink(missing_ok=True)
        except Exception:
            pass

    out_info = get_video_info(out_path)
    export_entry = {
        "filename": out_name,
        "layout_mode": layout_mode,
        "size_mb": round(out_path.stat().st_size / (1024 * 1024), 2),
        "duration": out_info["duration"],
        "speed": speed,
        "captions_burned": captions_burned,
        "created_at": int(time.time()),
    }
    project = load_project(project_id)  # reload: UI may have saved edits meanwhile
    project.setdefault("exports", []).insert(0, export_entry)
    project["layout_mode"] = layout_mode
    project["crop_x"] = cx
    project["font_size"] = font_size
    project["caption_style"] = caption_style
    project["viral_kit"] = generate_viral_kit(project)
    if "ship" not in project["stage_completed"]:
        project["stage_completed"].append("ship")
    save_project(project_id, project)

    if progress:
        warn = "" if captions_burned or not shifted_caps else " (sans sous-titres: échec libass)"
        progress("05 / SHIP", 100, f"READY TO POST — {out_name} ({export_entry['size_mb']} MB, {out_info['duration']}s){warn}", active_repos)
    return {"project": project, "export": export_entry}


def stage_05_batch_ship(
    project_id: str,
    layout_mode: str = "FACECAM + GAME + CAPS",
    crop_x: float | None = None,
    font_size: int = 40,
    caption_style: str = "pink",
    playback_speed: float = 1.0,
    upscale_sharpen: bool = False,
    retention_bar: bool = True,
    normalize_audio: bool = True,
    tracked: dict[str, Any] | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    project = load_project(project_id)
    moments = project.get("moments") or []
    dur = float(project["info"]["duration"] or 18.0)
    if not moments:
        step = dur / 3.0
        moments = [{"start": i * step, "end": min(dur, (i + 1) * step)} for i in range(3)]

    exports = []
    total = min(3, len(moments))
    for i, m in enumerate(moments[:total]):
        s_t = float(m["start"])
        e_t = min(float(m["end"]), s_t + 15.0)
        label = f"SHORT_0{i+1}"
        if progress:
            progress("05 / SHIP", max(5, int(i / total * 100)), f"Batch {i+1}/{total}: {label} ({s_t:.1f}s–{e_t:.1f}s)...", ["20"])
        res = stage_05_ship(
            project_id=project_id, layout_mode=layout_mode, crop_x=crop_x, font_size=font_size,
            caption_style=caption_style, playback_speed=playback_speed, upscale_sharpen=upscale_sharpen,
            retention_bar=retention_bar, normalize_audio=normalize_audio,
            start_sec=s_t, end_sec=e_t, short_label=label, tracked=tracked, progress=None,
        )
        exports.append(res["export"])

    final_proj = load_project(project_id)
    if progress:
        progress("05 / SHIP", 100, f"BATCH DONE — {len(exports)} Shorts 9:16 ready to post!", ["19", "20"])
    return {"project": final_proj, "exports": exports}


def stage_remotion_render(
    project_id: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """18 Remotion: real composition = clean 9:16 highlight video (FFmpeg) + React animated captions on top."""
    project = load_project(project_id)
    pdir = PROJECTS_DIR / project_id
    active_repos = ["18", "20"]
    dur = float(project["info"]["duration"] or 10.0)
    hl = project.get("highlight") or {}
    s_t = float(hl.get("start", 0.0))
    e_t = min(dur, float(hl.get("end", min(dur, 15.0))), s_t + 20.0)
    if e_t - s_t < 1.0:
        s_t, e_t = 0.0, min(dur, 15.0)

    out_name = f"REMOTION_{project_id}_{int(time.time())}.mp4"
    out_path = OUTPUTS_DIR / out_name

    # Serialize access to shared remotion/public/base.mp4 & remotion/src/captions.json
    with _REMOTION_LOCK:
        public_dir = REMOTION_DIR / "public"
        public_dir.mkdir(parents=True, exist_ok=True)
        base_mp4 = public_dir / "base.mp4"

        if progress:
            progress("05 / SHIP", 10, "20 FFmpeg preparing clean 9:16 base clip for Remotion...", ["20"])
        split_mode = "FACECAM" in (project.get("layout_mode") or "FACECAM").upper()
        res = _render_vertical(
            project=project, src_video=pdir / project["video_file"], out_path=base_mp4,
            s_t=s_t, e_t=e_t, cx=float(project.get("crop_x", 0.5)), split_mode=split_mode,
        )
        if res.returncode != 0 or not base_mp4.exists():
            raise RuntimeError(f"Base clip failed: {res.stderr[-400:]}")

        fps = 30
        caps = []
        for c in project.get("captions", []):
            cs, ce = float(c["start"]), float(c["end"])
            if ce <= s_t or cs >= e_t:
                continue
            caps.append({"id": c.get("id"), "text": c.get("text", ""), "start": round(max(cs, s_t) - s_t, 3), "end": round(min(ce, e_t) - s_t, 3)})
        duration_frames = max(30, int(round((e_t - s_t) * fps)))
        style = CAPTION_STYLE_HEX.get(project.get("caption_style", "pink"), CAPTION_STYLE_HEX["pink"])

        remotion_src = REMOTION_DIR / "src"
        remotion_src.mkdir(parents=True, exist_ok=True)
        (remotion_src / "captions.json").write_text(
            json.dumps(
                {"project_id": project_id, "fps": fps, "durationInFrames": duration_frames, "style": style, "captions": caps},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        if progress:
            progress("05 / SHIP", 40, f"18 Remotion rendering React composition ({duration_frames} frames, 1080x1920)...", active_repos)

        npx_cmd = shutil.which("npx.cmd") or shutil.which("npx") or "npx"
        res = _run_cmd(
            [npx_cmd, "remotion", "render", "src/Root.tsx", "Short01", str(out_path), "--log=error"],
            cwd=REMOTION_DIR,
            timeout=1200,
        )
        if res.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"Remotion render failed: {(res.stderr or res.stdout)[-500:]}")

    export_entry = {
        "filename": out_name,
        "layout_mode": "18 REMOTION REACT",
        "size_mb": round(out_path.stat().st_size / (1024 * 1024), 2),
        "duration": get_video_info(out_path)["duration"],
        "speed": 1.0,
        "captions_burned": True,
        "created_at": int(time.time()),
    }
    project = load_project(project_id)
    project.setdefault("exports", []).insert(0, export_entry)
    save_project(project_id, project)
    if progress:
        progress("05 / SHIP", 100, f"18 REMOTION READY — {out_name} ({export_entry['size_mb']} MB)", active_repos)
    return {"project": project, "export": export_entry}


CAPTION_STYLE_HEX = {
    "pink": {"fg": "#ee1d6c", "bg": "#ffffff"},
    "yellow": {"fg": "#facc15", "bg": "#111111"},
    "cyan": {"fg": "#22d3ee", "bg": "#111111"},
    "white": {"fg": "#ffffff", "bg": "#111111"},
    "tiktok_white": {"fg": "#ffffff", "bg": "transparent", "outline": True, "italic": True},
    "tiktok_yellow": {"fg": "#ffe600", "bg": "transparent", "outline": True, "italic": True},
}
