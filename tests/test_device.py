"""Compute-device autodetection, with CUDA / ctranslate2 mocked."""
from __future__ import annotations

import device


def test_auto_falls_back_to_cpu(monkeypatch):
    monkeypatch.delenv("CLIP_MASTER_DEVICE", raising=False)
    monkeypatch.setattr(device, "probe_cuda", lambda: {"available": False, "count": 0, "source": ""})
    st = device.device_status()
    assert st["requested"] == "auto"
    assert st["device"] == "cpu"
    assert st["whisper"] == {"device": "cpu", "compute_type": "int8"}
    assert st["yolo"]["device"] == "cpu"
    assert st["directml"] is False
    assert device.whisper_settings()["compute_type"] == "int8"
    assert device.yolo_device() == "cpu"


def test_auto_uses_cuda_when_probe_succeeds(monkeypatch):
    monkeypatch.setenv("CLIP_MASTER_DEVICE", "auto")
    monkeypatch.setattr(device, "probe_cuda", lambda: {"available": True, "count": 1, "source": "torch.cuda"})
    st = device.device_status()
    assert st["device"] == "cuda"
    assert st["cuda_available"] is True
    assert st["whisper"] == {"device": "cuda", "compute_type": "float16"}
    assert st["yolo"]["device"] == 0
    assert "torch.cuda" in st["reason"]
    assert device.yolo_device() == 0


def test_cpu_override_ignores_cuda(monkeypatch):
    monkeypatch.setenv("CLIP_MASTER_DEVICE", "cpu")
    monkeypatch.setattr(device, "probe_cuda", lambda: {"available": True, "count": 2, "source": "torch.cuda"})
    st = device.device_status()
    assert st["device"] == "cpu" and st["requested"] == "cpu"
    assert st["whisper"]["compute_type"] == "int8"
    assert st["yolo"]["device"] == "cpu"


def test_cuda_override_falls_back_when_missing(monkeypatch):
    monkeypatch.setenv("CLIP_MASTER_DEVICE", "cuda")
    monkeypatch.setattr(device, "probe_cuda", lambda: {"available": False, "count": 0, "source": ""})
    st = device.device_status()
    assert st["device"] == "cpu"
    assert "falling back" in st["reason"]


def test_invalid_device_treated_as_auto(monkeypatch):
    monkeypatch.setenv("CLIP_MASTER_DEVICE", "gpu")
    assert device.requested_device() == "auto"


def test_probe_cuda_uses_torch_and_ctranslate2(monkeypatch):
    class _Cuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def device_count():
            return 2

    class _Torch:
        cuda = _Cuda

    class _Ct2:
        @staticmethod
        def get_cuda_device_count():
            return 1

    import sys
    monkeypatch.setitem(sys.modules, "torch", _Torch)
    monkeypatch.setitem(sys.modules, "ctranslate2", _Ct2)
    probe = device.probe_cuda()
    assert probe["available"] is True
    assert probe["count"] == 2
    assert "torch.cuda" in probe["source"] and "ctranslate2" in probe["source"]


def test_probe_cuda_survives_missing_libs(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    # Force import failure by making __import__ skip them — probe already catches Exception
    # If modules are None, `import torch` may succeed if already imported. Stub the internals.
    def boom(name, *a, **k):
        raise ImportError(name)

    monkeypatch.setattr(device, "probe_cuda", device.probe_cuda)  # keep real
    # Call real probe; if torch is already imported in this process it may still see a GPU.
    # The function must not raise either way.
    assert set(device.probe_cuda()) >= {"available", "count", "source"}


def test_whisper_model_uses_resolved_device(monkeypatch):
    import pipeline

    captured = {}

    class FakeWhisper:
        def __init__(self, model_size, **kw):
            captured["size"] = model_size
            captured["kw"] = kw

    monkeypatch.setattr(device, "whisper_settings", lambda: {"device": "cuda", "compute_type": "float16"})
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", type("m", (), {"WhisperModel": FakeWhisper}))
    # faster_whisper may already be imported; patch the symbol used inside the function via a fake module
    import sys
    import types
    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeWhisper
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    pipeline._WHISPER_CACHE.clear()
    model = pipeline.get_whisper_model("tiny")
    assert isinstance(model, FakeWhisper)
    assert captured["kw"]["device"] == "cuda"
    assert captured["kw"]["compute_type"] == "float16"
    assert "cpu_threads" not in captured["kw"]


def test_mode_and_health_expose_device(client, monkeypatch):
    monkeypatch.setattr(device, "probe_cuda", lambda: {"available": False, "count": 0, "source": ""})
    monkeypatch.setenv("CLIP_MASTER_DEVICE", "auto")
    mode = client.get("/api/mode").json()
    assert mode["transcription"] == "local"
    assert "device" in mode and mode["device"]["device"] in {"cpu", "cuda"}
    assert "whisper" in mode["device"] and "disk" in mode
    health = client.get("/api/health").json()
    assert "device" in health and "disk" in health
