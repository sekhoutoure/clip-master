"""Export quality control: resolution, fps, duration, loudness (LUFS), freeze and black-frame detection.

Events in the first/last `edge` seconds are ignored (encoder/transition artefacts) and short holds are
reported as warnings instead of failures.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import cv2

from tools_registry import get_ffmpeg_path

OW, OH, OFPS = 1080, 1920, 30
TARGET_LUFS = -14.0


def _run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    kw: dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace", **kw)


def _qc_filter_events(starts: list, ends: list | None, duration: float, edge: float = 0.3,
                     min_hold: float = 1.5) -> tuple[list[str], list[str]]:
    """Drop events in the first/last `edge` seconds; short holds become warnings, not failures."""
    fails: list[str] = []
    warns: list[str] = []
    for i, raw in enumerate(starts or []):
        try:
            t0 = float(raw)
        except (TypeError, ValueError):
            continue
        try:
            t1 = float(ends[i]) if ends and i < len(ends) else t0 + min_hold
        except (TypeError, ValueError, IndexError):
            t1 = t0 + min_hold
        hold = max(0.0, t1 - t0)
        if t1 <= edge or t0 >= max(0.0, float(duration) - edge):
            continue
        if hold + 1e-6 < min_hold:
            warns.append(f"{t0:.1f}")
        else:
            fails.append(f"{t0:.1f}")
    return fails, warns


def qc_verdict(rep: dict[str, Any], min_duration: float | None = None, max_duration: float | None = None,
               target_lufs: float = TARGET_LUFS) -> tuple[bool, list[str]]:
    """QC: 1080x1920 30fps, optional duration window, measurable loudness near target, no freeze, no black frames."""
    failures: list[str] = []
    if rep.get("width") != OW or rep.get("height") != OH:
        failures.append(f"résolution {rep.get('width')}x{rep.get('height')} (attendu {OW}x{OH})")
    fps = float(rep.get("fps") or 0)
    if abs(fps - OFPS) >= 0.5:
        failures.append(f"fps {fps} (attendu {OFPS})")
    dur = float(rep.get("duration") or 0.0)
    if dur <= 0:
        failures.append("durée manquante ou invalide")
    else:
        if min_duration is not None and dur < min_duration - 0.5:
            failures.append(f"durée {dur:.1f}s trop courte (min {min_duration:.0f}s)")
        if max_duration is not None and dur > max_duration + 0.5:
            failures.append(f"durée {dur:.1f}s trop longue (max {max_duration:.0f}s)")
    lufs = rep.get("lufs_I")
    if lufs is None:
        failures.append("loudness (LUFS) manquante")
    else:
        try:
            if abs(float(lufs) - target_lufs) > 1.5:
                failures.append(f"loudness {lufs} LUFS (cible {target_lufs}, tolérance 1.5)")
        except (TypeError, ValueError):
            failures.append("loudness (LUFS) manquante")
    if rep.get("freezes"):
        failures.append(f"gel d'image à {', '.join(map(str, rep['freezes']))}s")
    if rep.get("black"):
        failures.append(f"écran noir à {', '.join(map(str, rep['black']))}s")
    return (not failures, failures)


def qc_report(path: Path, min_duration: float | None = None, max_duration: float | None = None,
              target_lufs: float = TARGET_LUFS) -> dict[str, Any]:
    ffmpeg = get_ffmpeg_path()
    rep: dict[str, Any] = {}
    cap = cv2.VideoCapture(str(path))
    rep["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    rep["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    rep["fps"] = round(float(cap.get(cv2.CAP_PROP_FPS) or 0), 2)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    rep["duration"] = round(frames / rep["fps"], 2) if rep["fps"] else 0.0
    cap.release()
    e = _run([ffmpeg, "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr
    try:
        rep["lufs_I"] = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", e)[-1])
        rep["true_peak_dbfs"] = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", e)[-1])
    except Exception:
        rep["lufs_I"] = None
    f = _run([ffmpeg, "-hide_banner", "-i", str(path),
              "-vf", "freezedetect=n=0.003:d=1.2,blackdetect=d=0.8:pix_th=0.08",
              "-an", "-f", "null", "-"]).stderr
    freeze_s = re.findall(r"freeze_start:\s*([\d.]+)", f)
    freeze_e = re.findall(r"freeze_end:\s*([\d.]+)", f)
    black_s = re.findall(r"black_start:\s*([\d.]+)", f)
    black_e = re.findall(r"black_end:\s*([\d.]+)", f)
    dur = float(rep.get("duration") or 0.0)
    freeze_fail, freeze_warn = _qc_filter_events(freeze_s, freeze_e, dur, edge=0.3, min_hold=1.5)
    black_fail, black_warn = _qc_filter_events(black_s, black_e, dur, edge=0.3, min_hold=0.8)
    rep["freezes"] = freeze_fail
    rep["black"] = black_fail
    rep["warnings"] = []
    if freeze_warn:
        rep["warnings"].append("gels courts ignorés à " + ", ".join(t + "s" for t in freeze_warn))
    if black_warn:
        rep["warnings"].append("noirs courts ignorés à " + ", ".join(t + "s" for t in black_warn))
    ok, failures = qc_verdict(rep, min_duration, max_duration, target_lufs)
    rep["pass"] = bool(ok)
    rep["failures"] = failures
    return rep


