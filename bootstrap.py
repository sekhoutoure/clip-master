"""CLIP MASTER bootstrap + doctor. Standard library only (PyYAML is used when present, never required).

    python bootstrap.py              # finish the setup: .env, Remotion deps, model weights, third-party tools
    python bootstrap.py --check      # doctor: report everything, change nothing (exit 1 if a required item is missing)
    python bootstrap.py --with-reference   # also clone the reference-only sources listed in third_party/manifest.yml
    python bootstrap.py --json       # machine-readable doctor output (for coding agents)

`install.sh` / `install.ps1` create the virtualenv, install requirements.txt and then call this script.
The same code backs `clip-master install` and `clip-master doctor`.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "third_party" / "manifest.yml"
THIRD_PARTY = ROOT / "third_party"
MODELS = Path(os.environ.get("CLIP_MASTER_MODELS_DIR") or (Path(os.environ.get("CLIP_MASTER_DATA_DIR") or ROOT) / "models")).expanduser()

YOLO_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt"
FACE_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/"
            "float16/1/blaze_face_short_range.tflite")

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
_ICON = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]", INFO: "[info]"}

# Python modules that must import for the app to work (pip name -> import name)
PY_MODULES = {
    "fastapi": "fastapi", "uvicorn": "uvicorn", "python-multipart": "multipart", "python-dotenv": "dotenv",
    "opencv-python": "cv2", "numpy": "numpy", "scipy": "scipy", "pillow": "PIL", "imageio-ffmpeg": "imageio_ffmpeg",
    "mediapipe": "mediapipe", "ultralytics": "ultralytics", "faster-whisper": "faster_whisper",
    "yt-dlp": "yt_dlp", "streamlink": "streamlink", "scenedetect": "scenedetect", "auto-editor": "auto_editor",
}


# --------------------------------------------------------------------------- manifest
def _parse_simple_yaml(text: str) -> list[dict[str, Any]]:
    """Reads the flat '- key: value' list used by manifest.yml (fallback when PyYAML is missing)."""
    items: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^- (\w+):\s*(.*)$", line)
        if m:
            cur = {}
            items.append(cur)
        else:
            m = re.match(r"^\s+(\w+):\s*(.*)$", line)
        if not m or cur is None:
            raise ValueError(f"manifest.yml: cannot read line: {raw!r}")
        val: Any = m.group(2).strip()
        if val.lower() in ("true", "false"):
            val = val.lower() == "true"
        cur[m.group(1)] = val
    return items


def load_manifest(path: Path = MANIFEST) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except ImportError:
        data = _parse_simple_yaml(text)
    for it in data:
        for key in ("name", "kind", "license", "purpose"):
            if key not in it:
                raise ValueError(f"manifest.yml: entry {it.get('name', it)!r} lacks '{key}'")
    return data


def platform_key() -> str:
    sysname = platform.system().lower()
    mach = platform.machine().lower()
    arm = mach in ("arm64", "aarch64")
    if sysname == "windows":
        return "windows_x64"
    if sysname == "darwin":
        return "macos_arm64" if arm else "macos_x64"
    return "linux_arm64" if arm else "linux_x64"


# --------------------------------------------------------------------------- helpers
def _venv_python() -> Path | None:
    for cand in (ROOT / ".venv" / "bin" / "python", ROOT / ".venv" / "Scripts" / "python.exe"):
        if cand.exists():
            return cand
    return None


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)


def _download(url: str, dest: Path, sha256: str | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkstemp(dir=dest.parent, suffix=".part")[1])
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "clip-master-bootstrap"})
        h = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as f:
            while chunk := resp.read(1 << 20):
                h.update(chunk)
                f.write(chunk)
        if sha256 and h.hexdigest().lower() != sha256.lower():
            raise RuntimeError(f"checksum mismatch for {url} (expected {sha256[:12]}…, got {h.hexdigest()[:12]}…)")
        if tmp.stat().st_size == 0:
            raise RuntimeError(f"empty download from {url}")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


class Report:
    def __init__(self) -> None:
        self.rows: list[dict[str, str]] = []

    def add(self, status: str, name: str, detail: str = "", fix: str = "") -> None:
        self.rows.append({"status": status, "name": name, "detail": detail, "fix": fix})

    @property
    def failed(self) -> bool:
        return any(r["status"] == FAIL for r in self.rows)

    def print(self) -> None:
        for r in self.rows:
            print(f"{_ICON[r['status']]} {r['name']:<28} {r['detail']}")
            if r["fix"] and r["status"] in (FAIL, WARN):
                print(f"       -> {r['fix']}")


# --------------------------------------------------------------------------- checks
def check_python(rep: Report) -> None:
    v = sys.version_info
    label = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 10):
        rep.add(FAIL, "Python", label, "Install Python 3.10-3.12 (https://www.python.org/downloads/)")
    elif v >= (3, 13):
        rep.add(WARN, "Python", f"{label} (3.10-3.12 recommended)",
                "MediaPipe / PyTorch wheels may be missing: use python3.12 -m venv .venv")
    else:
        rep.add(OK, "Python", label)


def find_ffmpeg() -> tuple[str | None, str]:
    env = os.environ.get("FFMPEG_PATH", "").strip()
    if env and Path(env).exists():
        return env, ".env FFMPEG_PATH"
    for cand in (ROOT / "bin" / "ffmpeg", ROOT / "bin" / "ffmpeg.exe"):
        if cand.exists():
            return str(cand), "bin/"
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff, "PATH"
    if importlib.util.find_spec("imageio_ffmpeg"):
        try:
            import imageio_ffmpeg  # type: ignore

            return imageio_ffmpeg.get_ffmpeg_exe(), "imageio-ffmpeg (bundled)"
        except Exception:
            pass
    return None, ""


def check_ffmpeg(rep: Report) -> None:
    path, origin = find_ffmpeg()
    if not path:
        rep.add(FAIL, "FFmpeg", "not found", ffmpeg_install_hint())
        return
    try:
        out = _run([path, "-hide_banner", "-filters"], timeout=30).stdout
        ver = _run([path, "-version"], timeout=30).stdout.splitlines()[0]
    except Exception as exc:  # noqa: BLE001
        rep.add(FAIL, "FFmpeg", f"{path} does not run: {exc}", ffmpeg_install_hint())
        return
    missing = [f for f in ("subtitles", "loudnorm", "minterpolate") if f not in out]
    if missing:
        rep.add(WARN, "FFmpeg", f"{ver[:40]} - missing filters: {', '.join(missing)}",
                "Install a full FFmpeg build (with libass) - " + ffmpeg_install_hint())
    else:
        rep.add(OK, "FFmpeg", f"{ver[:48]} ({origin})")


def ffmpeg_install_hint() -> str:
    sysname = platform.system()
    if sysname == "Windows":
        return "winget install Gyan.FFmpeg   (or install.ps1 -Yes)"
    if sysname == "Darwin":
        return "brew install ffmpeg"
    return "sudo apt install ffmpeg   (Debian/Ubuntu) | sudo dnf install ffmpeg | sudo pacman -S ffmpeg"


def node_version() -> tuple[int, ...] | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", _run([node, "--version"], timeout=20).stdout)
        return tuple(int(x) for x in m.groups()) if m else None
    except Exception:  # noqa: BLE001
        return None


def check_node(rep: Report) -> None:
    v = node_version()
    if v is None:
        rep.add(WARN, "Node.js (optional)", "not found - only needed for Remotion captions",
                "winget install OpenJS.NodeJS.LTS | brew install node | sudo apt install nodejs npm")
    elif v[0] < 18:
        rep.add(WARN, "Node.js (optional)", ".".join(map(str, v)) + " (18+ needed for Remotion)", "Upgrade Node.js to an LTS version")
    else:
        rep.add(OK, "Node.js (optional)", ".".join(map(str, v)))


def check_venv_and_modules(rep: Report) -> None:
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    vpy = _venv_python()
    rep.add(OK if (in_venv or vpy) else WARN, "Virtualenv", ".venv found" if vpy else ("active" if in_venv else "none"),
            "Run ./install.sh (Linux/macOS) or install.ps1 (Windows)")
    missing = [pip for pip, mod in PY_MODULES.items() if importlib.util.find_spec(mod) is None]
    if missing:
        rep.add(FAIL, "Python packages", "missing: " + ", ".join(missing), "pip install -r requirements.txt  (inside the venv)")
    else:
        rep.add(OK, "Python packages", f"{len(PY_MODULES)} required packages import")


def check_remotion(rep: Report) -> None:
    ok = (ROOT / "remotion" / "node_modules" / "@remotion" / "cli").exists()
    rep.add(OK if ok else INFO, "Remotion deps (optional)", "installed" if ok else "not installed",
            "cd remotion && npm install")


def check_models(rep: Report) -> None:
    for name in ("yolov8n.pt", "blaze_face_short_range.tflite"):
        p = MODELS / name
        rep.add(OK if p.exists() and p.stat().st_size > 1000 else WARN, f"Model {name}",
                f"{p.stat().st_size // 1024} KB" if p.exists() else "not downloaded (auto-downloads on first use)",
                "python bootstrap.py")


def check_env(rep: Report) -> None:
    env = ROOT / ".env"
    rep.add(OK if env.exists() else WARN, ".env", "present" if env.exists() else "missing",
            "cp .env.example .env  (python bootstrap.py does it)")
    key = os.environ.get("GROQ_API_KEY", "")
    if env.exists() and not key:
        m = re.search(r"^GROQ_API_KEY=(.+)$", env.read_text(encoding="utf-8", errors="ignore"), re.M)
        key = m.group(1).strip() if m else ""
    rep.add(INFO, "AI mode", "cloud (Groq)" if key else "local (Ollama, no key) - transcription is always local")


def third_party_status(entry: dict[str, Any]) -> tuple[str, str]:
    d = THIRD_PARTY / entry["name"]
    marker = d / ".clipmaster-ref"
    if marker.exists() and marker.read_text().strip() == str(entry.get("ref", "")):
        return OK, f"{entry.get('ref')}"
    return INFO, "not fetched"


def check_third_party(rep: Report, with_reference: bool) -> None:
    for e in load_manifest():
        if e["kind"] == "binary":
            st, detail = third_party_status(e)
            rep.add(OK if st == OK else WARN, f"{e['name']} ({e['license']})", detail if st == OK else "not fetched (optional)",
                    "python bootstrap.py")
        elif e["kind"] == "git":
            st, detail = third_party_status(e)
            if with_reference or st == OK:
                rep.add(st, f"{e['name']} (reference)", detail, "python bootstrap.py --with-reference")


def doctor(with_reference: bool = False) -> Report:
    rep = Report()
    check_python(rep)
    check_venv_and_modules(rep)
    check_ffmpeg(rep)
    check_node(rep)
    check_remotion(rep)
    check_models(rep)
    check_env(rep)
    check_third_party(rep, with_reference)
    return rep


# --------------------------------------------------------------------------- actions
def ensure_env() -> str:
    env, example = ROOT / ".env", ROOT / ".env.example"
    if env.exists():
        return "kept existing .env"
    if example.exists():
        shutil.copy2(example, env)
        return "created .env from .env.example"
    return "no .env.example found"


def ensure_remotion() -> str:
    if (ROOT / "remotion" / "node_modules" / "@remotion" / "cli").exists():
        return "already installed"
    v = node_version()
    if v is None or v[0] < 18:
        return "skipped (Node.js 18+ not found - Remotion is optional)"
    npm = shutil.which("npm")
    if not npm:
        return "skipped (npm not found)"
    cmd = [npm, "ci" if (ROOT / "remotion" / "package-lock.json").exists() else "install", "--no-audit", "--no-fund"]
    res = _run(cmd, cwd=ROOT / "remotion", timeout=1800)
    if res.returncode != 0:
        raise RuntimeError(f"npm failed: {(res.stderr or res.stdout)[-400:]}")
    return "installed"


def ensure_models() -> list[str]:
    msgs = []
    for name, url in (("yolov8n.pt", YOLO_URL), ("blaze_face_short_range.tflite", FACE_URL)):
        dest = MODELS / name
        if dest.exists() and dest.stat().st_size > 1000:
            msgs.append(f"{name}: already present")
            continue
        _download(url, dest)
        msgs.append(f"{name}: downloaded")
    return msgs


def fetch_binary(entry: dict[str, Any]) -> str:
    key = platform_key()
    asset = entry.get(f"asset_{key}")
    if not asset:
        return f"skipped (no build for {key})"
    dest = THIRD_PARTY / entry["name"]
    marker = dest / ".clipmaster-ref"
    if marker.exists() and marker.read_text().strip() == str(entry["ref"]):
        return f"already at {entry['ref']}"
    url = entry["url"].format(ref=entry["ref"], asset=asset)
    with tempfile.TemporaryDirectory() as td:
        zpath = Path(td) / asset
        _download(url, zpath, entry.get(f"sha256_{key}"))
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        with zipfile.ZipFile(zpath) as z:
            for member in z.infolist():  # refuse path traversal
                target = (dest / member.filename).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise RuntimeError(f"unsafe path in archive: {member.filename}")
            z.extractall(dest)
    for f in dest.iterdir():
        if f.is_file() and not f.suffix:
            f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    marker.write_text(str(entry["ref"]))
    return f"installed {entry['ref']} -> third_party/{entry['name']}/"


def fetch_git(entry: dict[str, Any]) -> str:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git not found (install git to fetch reference sources)")
    dest = THIRD_PARTY / entry["name"]
    marker = dest / ".clipmaster-ref"
    ref = str(entry["ref"])
    if marker.exists() and marker.read_text().strip() == ref:
        return f"already at {ref}"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for cmd in (["init", "-q"], ["remote", "add", "origin", entry["url"]], ["fetch", "-q", "--depth", "1", "origin", ref],
                ["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"]):
        res = _run([git, *cmd], cwd=dest, timeout=900)
        if res.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError(f"git {cmd[0]} failed for {entry['name']}: {res.stderr.strip()[-300:]}")
    marker.write_text(ref)
    return f"cloned {ref}"


def install(with_reference: bool = False) -> int:
    errors: list[str] = []

    def step(label: str, fn) -> None:
        try:
            out = fn()
            for line in (out if isinstance(out, list) else [out]):
                print(f"[ ok ] {label}: {line}")
        except Exception as exc:  # noqa: BLE001 - keep going, report all problems at the end
            errors.append(f"{label}: {exc}")
            print(f"[FAIL] {label}: {exc}")

    step(".env", ensure_env)
    step("Remotion", ensure_remotion)
    step("Model weights", ensure_models)
    for e in load_manifest():
        if e["kind"] == "binary":
            step(e["name"], lambda e=e: fetch_binary(e))
        elif e["kind"] == "git" and (with_reference or e.get("default", True) is True):
            step(e["name"], lambda e=e: fetch_git(e))
    print()
    rep = doctor(with_reference)
    rep.print()
    if errors:
        print("\nProblems:\n  - " + "\n  - ".join(errors))
        print("Fix the lines above and re-run: python bootstrap.py  (it is safe to run again)")
        return 1
    return 1 if rep.failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CLIP MASTER bootstrap / doctor")
    ap.add_argument("--check", action="store_true", help="doctor mode: report only, change nothing")
    ap.add_argument("--with-reference", action="store_true", help="also fetch the reference-only sources (third_party/manifest.yml)")
    ap.add_argument("--json", action="store_true", help="machine-readable output (implies --check)")
    args = ap.parse_args(argv)
    if args.check or args.json:
        rep = doctor(args.with_reference)
        if args.json:
            print(json.dumps({"ok": not rep.failed, "checks": rep.rows}, indent=2))
        else:
            rep.print()
            print("\nAll required checks passed." if not rep.failed else "\nSome required checks failed (see [FAIL] lines).")
        return 1 if rep.failed else 0
    return install(args.with_reference)


if __name__ == "__main__":
    sys.exit(main())
