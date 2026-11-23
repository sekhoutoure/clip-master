"""Hardware-device selection for transcription and subject tracking.

``CLIP_MASTER_DEVICE``: ``auto`` (default) uses CUDA when PyTorch or ctranslate2
can see a GPU, otherwise CPU. ``cpu`` and ``cuda`` force a choice; ``cuda``
falls back to CPU if no device is found. DirectML is not auto-enabled (see README).
"""
from __future__ import annotations

import os
from typing import Any

VALID_DEVICES = ("auto", "cpu", "cuda")


def requested_device() -> str:
    raw = (os.getenv("CLIP_MASTER_DEVICE") or "auto").strip().lower()
    return raw if raw in VALID_DEVICES else "auto"


def probe_cuda() -> dict[str, Any]:
    """Best-effort CUDA probe. Never raises. Does not initialize a full model."""
    sources: list[str] = []
    count = 0

    try:
        import torch

        if torch.cuda.is_available() and int(torch.cuda.device_count()) > 0:
            count = max(count, int(torch.cuda.device_count()))
            sources.append("torch.cuda")
    except Exception:
        pass

    try:
        import ctranslate2

        getter = getattr(ctranslate2, "get_cuda_device_count", None)
        if callable(getter):
            n = int(getter())
            if n > 0:
                count = max(count, n)
                sources.append("ctranslate2")
    except Exception:
        pass

    return {
        "available": count > 0,
        "count": count,
        "source": "+".join(sources) if sources else "",
    }


def device_status() -> dict[str, Any]:
    """Resolved device + per-tool settings, for /api/mode and `clip-master doctor`."""
    req = requested_device()
    probe = probe_cuda()
    cuda_ok = bool(probe["available"])

    if req == "cpu":
        device = "cpu"
        reason = "CLIP_MASTER_DEVICE=cpu"
    elif req == "cuda":
        if cuda_ok:
            device = "cuda"
            reason = f"CLIP_MASTER_DEVICE=cuda ({probe['source']}, {probe['count']} device(s))"
        else:
            device = "cpu"
            reason = "CLIP_MASTER_DEVICE=cuda but no CUDA device found; falling back to CPU"
    elif cuda_ok:
        device = "cuda"
        reason = f"auto: CUDA detected via {probe['source']} ({probe['count']} device(s))"
    else:
        device = "cpu"
        reason = "auto: no CUDA device, using CPU"

    compute_type = "float16" if device == "cuda" else "int8"
    yolo = 0 if device == "cuda" else "cpu"
    return {
        "requested": req,
        "device": device,
        "cuda_available": cuda_ok,
        "cuda_source": probe["source"],
        "cuda_device_count": probe["count"],
        "whisper": {"device": device, "compute_type": compute_type},
        "yolo": {"device": yolo},
        "reason": reason,
        "directml": False,
    }


def whisper_settings() -> dict[str, str]:
    w = device_status()["whisper"]
    return {"device": str(w["device"]), "compute_type": str(w["compute_type"])}


def yolo_device() -> int | str:
    return device_status()["yolo"]["device"]
