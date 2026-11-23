"""Audio alignment between two versions of the same video (e.g. a 1080p download and a low-res proxy).

`audio_lag_seconds` cross-correlates the mono audio of both files (FFT) and returns the lag in
seconds, so you can trim the start of one version to line it up with the other.
"""
from __future__ import annotations

import os
import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np

from tools_registry import get_ffmpeg_path


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    kw: dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace", **kw)


ALIGN_PAD_S = 8.0
ALIGN_MIN_SCORE = 0.25


def _extract_pcm16(path: Path, sr: int = 8000) -> np.ndarray:
    wav = path.with_name(path.stem + f"_{sr}_align.wav")
    try:
        r = _run([get_ffmpeg_path(), "-v", "error", "-y", "-i", str(path), "-ac", "1", "-ar", str(sr),
                  "-c:a", "pcm_s16le", str(wav)], timeout=120)
        if r.returncode != 0 or not wav.exists():
            return np.zeros(0, dtype=np.float32)
        with wave.open(str(wav)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    finally:
        wav.unlink(missing_ok=True)


def audio_lag_seconds(hi_mp4: Path, ref_mp4: Path, sr: int = 8000, max_lag_s: float = 10.0) -> tuple[float | None, float]:
    """Lag in seconds of the hi-res file vs the low-res reference (positive = trim the start of the hi-res file)."""
    a = _extract_pcm16(hi_mp4, sr)
    b = _extract_pcm16(ref_mp4, sr)
    if a.size < sr or b.size < sr:
        return None, 0.0
    a = a - float(a.mean())
    b = b - float(b.mean())
    a /= (np.linalg.norm(a) + 1e-9)
    b /= (np.linalg.norm(b) + 1e-9)
    n = int(len(a) + len(b) - 1)
    nfft = 1 << (n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(a, nfft) * np.conj(np.fft.rfft(b, nfft)), nfft)[:n]
    lags = np.arange(-(len(b) - 1), len(a))
    max_lag = int(max_lag_s * sr)
    mask = np.abs(lags) <= max_lag
    if not mask.any():
        return None, 0.0
    idx = int(np.argmax(corr[mask]))
    lag = int(lags[mask][idx])
    score = float(corr[mask][idx])
    if score < ALIGN_MIN_SCORE:
        return None, score
    return lag / float(sr), score
