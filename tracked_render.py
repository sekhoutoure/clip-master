"""Tracked 9:16 renderer: subject tracking, facecam inset, word-pop captions, loudness-normalised export.

Pipeline (two passes):
  1. analyse_segment()      subject boxes every N frames, scene cuts, automatic facecam detection,
                            per-scene smoothed crop position (median + gaussian + dead-zone + speed clamp)
  2. render_tracked_video() composes 1080x1920 frames: tracked gameplay crop + optional facecam inset on top
  3. render_tracked_short() burns word-timed captions (ASS), applies 2-pass loudnorm (-14 LUFS / TP -1.5)
                            and runs QC (see qc.py).

Entry point: render_tracked_short(...). Used by pipeline.stage_05_ship when the "tracked" mode is on.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from profanity import censor_text, censor_word
from qc import OFPS, OH, OW, qc_report
from tools_registry import get_ffmpeg_path

BASE_DIR = Path(__file__).resolve().parent
FONTS_DIR = BASE_DIR / "assets" / "fonts"

DEFAULT_OPTIONS: dict[str, Any] = {
    "enabled": False,          # tracked mode on/off for this export
    "strict": False,           # when True, a failed QC marks the export as REJECTED
    "facecam_inset": True,     # detect the facecam and show it on top of the 9:16 frame
    "inset_width": 0.46,       # facecam width as a fraction of the output width
    "tracked_crop": True,      # follow the main subject; otherwise static crop_x
    "yolo_stride": 2,          # run subject detection every N output frames (interpolated in between)
    "censor_captions": False,  # remove profanity from burned captions (audio untouched)
    "censor_mode": "remove",   # remove | mask
    "caption_color": "FFE600",
    "caption_font": "Anton",
    "caption_size": 150,
    "caption_ypos": 0.66,      # vertical position (fraction of height)
    "max_words": 4,            # words per caption pop
    "max_chars": 18,
    "min_duration": None,      # optional QC duration window (seconds)
    "max_duration": None,
}


def normalize_options(opts: dict[str, Any] | None) -> dict[str, Any]:
    o = dict(DEFAULT_OPTIONS)
    for k, v in (opts or {}).items():
        if k in o and v is not None:
            o[k] = v
    for k in ("enabled", "strict", "facecam_inset", "tracked_crop", "censor_captions"):
        o[k] = bool(o[k])
    o["inset_width"] = max(0.30, min(0.70, float(o["inset_width"] or 0.46)))
    o["max_words"] = max(1, min(6, int(o["max_words"] or 4)))
    o["max_chars"] = max(8, min(40, int(o["max_chars"] or 18)))
    o["yolo_stride"] = max(1, min(6, int(o["yolo_stride"] or 2)))
    o["caption_size"] = max(40, min(220, int(o["caption_size"] or 150)))
    o["caption_ypos"] = max(0.1, min(0.9, float(o["caption_ypos"] or 0.66)))
    o["censor_mode"] = "mask" if o["censor_mode"] == "mask" else "remove"
    col = str(o["caption_color"]).lstrip("#").upper()
    o["caption_color"] = col if re.fullmatch(r"[0-9A-F]{6}", col) else DEFAULT_OPTIONS["caption_color"]
    o["caption_font"] = re.sub(r"[^A-Za-z0-9 _\-]", "", str(o["caption_font"]))[:40] or "Anton"
    for k in ("min_duration", "max_duration"):
        o[k] = float(o[k]) if o[k] not in (None, "", 0) else None
    return o


def _ffmpeg_esc(p: Path | str) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    kw: dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace", **kw)


# ---------------------------------------------------------------------------
# Captions: grouping + pop style
# ---------------------------------------------------------------------------
def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _clean(t: str) -> str:
    t = re.sub(r'[.,!?;:"«»“”…]', "", t)
    return re.sub(r"\s+", " ", t.upper()).strip()


def caption_words(captions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten project captions to timed words. Uses word timings when the caption text
    was not edited in the UI, else spreads the (edited) text evenly over the caption."""
    out: list[dict[str, Any]] = []
    for c in captions:
        text = (c.get("text") or "").strip()
        ws = c.get("words") or []
        if ws and _clean(" ".join(w.get("word", "") for w in ws)) == _clean(text):
            for w in ws:
                out.append({"w": w["word"].strip(), "s": float(w["start"]), "e": float(w["end"])})
        elif text:
            toks = text.split()
            s, e = float(c["start"]), float(c["end"])
            step = max(0.05, (e - s) / max(1, len(toks)))
            for i, t in enumerate(toks):
                out.append({"w": t, "s": s + i * step, "e": s + (i + 1) * step})
    out.sort(key=lambda w: w["s"])
    # whisper splits "j'ai" into "j" + "'ai", "-moi" etc.: glue them back (captions.py merge_tokens)
    merged: list[dict[str, Any]] = []
    for w in out:
        t = w["w"]
        if not t or not re.search(r"[A-Za-zÀ-ÿ0-9]", t):
            continue
        if merged and (t[0] in "'’-" or merged[-1]["w"].endswith(("'", "’"))):
            merged[-1] = dict(merged[-1], w=merged[-1]["w"] + t, e=w["e"])
        else:
            merged.append(dict(w))
    return merged


def _group(words: list[dict[str, Any]], maxw: int, maxchars: int = 18) -> list[list[dict[str, Any]]]:
    phrases, cur = [], []
    for w in words:
        if cur and w["s"] - cur[-1]["e"] > 0.45:
            phrases.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?,…]$", w["w"]):
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)
    out = []
    for ph in phrases:
        k = -(-len(ph) // maxw)
        while True:
            size = -(-len(ph) // k)
            chunks = [ph[j:j + size] for j in range(0, len(ph), size)]
            if all(len(" ".join(x["w"] for x in c)) <= maxchars for c in chunks) or size == 1:
                break
            k += 1
        out.extend(chunks)
    return out



def write_caption_ass(captions: list[dict[str, Any]], out_path: Path, s_t: float, e_t: float, speed: float,
                      opts: dict[str, Any]) -> int:
    """Word-timed ASS 'pop' captions for the [s_t, e_t] window (output timeline stretched by 1/speed)."""
    censor = bool(opts.get("censor_captions"))
    mode = opts.get("censor_mode", "remove")
    words = []
    for w in caption_words(captions):
        if w["e"] <= s_t or w["s"] >= e_t:
            continue
        tok = censor_word(w["w"], mode) if censor else w["w"]
        if not tok.strip() or not re.search(r"[A-Za-zÀ-ÿ0-9*]", tok):
            continue
        words.append({"w": tok, "s": (max(w["s"], s_t) - s_t) / speed, "e": (min(w["e"], e_t) - s_t) / speed})
    color = opts.get("caption_color", "FFE600")
    rr, gg, bb = color[0:2], color[2:4], color[4:6]
    col = f"&H00{bb}{gg}{rr}"
    y = int(OH * float(opts.get("caption_ypos", 0.66)))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,{opts.get('caption_font', 'Anton')},{int(opts.get('caption_size', 150))},{col},{col},&H00000000,&H78000000,-1,0,0,0,100,100,1,0,1,0,4,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    gs = _group(words, int(opts.get("max_words", 4)), int(opts.get("max_chars", 18)))
    for i, g in enumerate(gs):
        s = g[0]["s"]
        e = max(g[-1]["e"] + 0.25, s + 0.35)
        if i + 1 < len(gs):
            e = min(e, gs[i + 1][0]["s"])
        txt = _clean(" ".join(w["w"] for w in g)).replace("{", "").replace("}", "")
        if censor:
            txt = censor_text(txt, mode)
        if not txt or e <= s:
            continue
        anim = r"{\an5\pos(540," + str(y) + r")\fscx72\fscy72\t(0,90,\fscx108\fscy108)\t(90,150,\fscx100\fscy100)}"
        lines.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Pop,,0,0,0,,{anim}{txt}")
    out_path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


# ---------------------------------------------------------------------------
# Tracking (port of track_crop.py)
# ---------------------------------------------------------------------------
def _find_facecam(dets: list[list[list[float]]], W: int, H: int) -> list[int] | None:
    sampled = [f for f in dets if f is not None]
    pts = [(b[0], b[1], b[2], b[3]) for f in sampled for b in f]
    if not pts:
        return None
    pts_a = np.array(pts)
    n_samples = len(sampled)
    best = None
    for p in pts_a:
        cx, cy = (p[0] + p[2]) / 2, (p[1] + p[3]) / 2
        d = np.hypot((pts_a[:, 0] + pts_a[:, 2]) / 2 - cx, (pts_a[:, 1] + pts_a[:, 3]) / 2 - cy)
        same = pts_a[d < 0.02 * W]
        if len(same) >= 0.5 * n_samples:
            area = ((p[2] - p[0]) * (p[3] - p[1])) / (W * H)
            edge = min(cx, W - cx) / W < 0.25 or min(cy, H - cy) / H < 0.25
            if area < 0.2 and edge and (best is None or len(same) > best[0]):
                best = (len(same), np.median(same, axis=0))
    if best is None:
        return None
    x1, y1, x2, y2 = best[1]
    w, h = x2 - x1, y2 - y1
    cx = (x1 + x2) / 2
    cw = max(w * 1.9, 0.18 * W)
    ch = cw * 9 / 16
    if ch < h * 1.15:
        ch = h * 1.15
        cw = ch * 16 / 9
    top = max(0, y2 - ch) if (y1 + y2) / 2 > H / 2 else max(0, y1 - 0.12 * ch)
    return [int(np.clip(cx - cw / 2, 0, W - cw)), int(np.clip(top, 0, H - ch)), int(cw), int(ch)]


def _refine_facecam(frames_gray: list[np.ndarray], rect: list[int], W: int, H: int) -> list[int]:
    x, y, w, h = rect
    X0, Y0 = max(0, int(x - 0.35 * w)), max(0, int(y - 0.35 * h))
    X1, Y1 = min(W, int(x + 1.35 * w)), min(H, int(y + 1.35 * h))
    crops = [f[Y0:Y1, X0:X1].astype(np.float32) for f in frames_gray]
    if len(crops) < 3:
        return rect
    med = np.median(np.stack(crops), axis=0)
    gx = np.abs(cv2.Sobel(med, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(med, cv2.CV_32F, 0, 1, ksize=3))
    col, row = gx.mean(axis=0), gy.mean(axis=1)
    cx0, cx1, cy0, cy1 = x - X0, x + w - X0, y - Y0, y + h - Y0

    def peak(prof, lo, hi):
        lo, hi = max(0, int(lo)), min(len(prof), int(hi))
        return lo + int(np.argmax(prof[lo:hi])) if hi > lo else None

    L = peak(col, cx0 - 0.3 * w, cx0 + 0.15 * w)
    R = peak(col, cx1 - 0.15 * w, cx1 + 0.3 * w)
    T = peak(row, cy0 - 0.3 * h, cy0 + 0.15 * h)
    B = peak(row, cy1 - 0.15 * h, cy1 + 0.3 * h)
    if None in (L, R, T, B) or R - L < 0.6 * w or B - T < 0.6 * h:
        return rect
    pad = 6
    return [X0 + L + pad, Y0 + T + pad, R - L - 2 * pad, B - T - 2 * pad]


def _inside(b, r) -> bool:
    if r is None:
        return False
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return r[0] <= cx <= r[0] + r[2] and r[1] <= cy <= r[1] + r[3]


def _smooth(xs, cuts_f, fps, cw, sigma=0.6, maxvel=0.9):
    from scipy.ndimage import gaussian_filter1d, median_filter

    xs = np.array(xs, float)
    out = np.empty_like(xs)
    bounds = [0] + [c for c in cuts_f if 0 < c < len(xs)] + [len(xs)]
    for a, b in zip(bounds[:-1], bounds[1:], strict=False):
        seg = xs[a:b]
        if len(seg) > 9:
            seg = median_filter(seg, size=9, mode="nearest")
        if len(seg) > 3:
            seg = gaussian_filter1d(seg, sigma * fps, mode="nearest")
        res = [seg[0]]
        vmax = maxvel * cw / fps
        for v in seg[1:]:
            d = v - res[-1]
            if abs(d) < 0.04 * cw:
                d = 0.15 * d
            res.append(res[-1] + float(np.clip(d, -vmax, vmax)))
        out[a:b] = res
    return out


class _FrameReader:
    """Yields source frames resampled to 30 fps CFR between s_t and e_t."""

    def __init__(self, path: Path, s_t: float, e_t: float):
        self.cap = cv2.VideoCapture(str(path))
        self.sfps = float(self.cap.get(cv2.CAP_PROP_FPS) or 30.0)
        self.W = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.H = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.s_t, self.e_t = s_t, e_t
        self.n_out = max(1, int(round((e_t - s_t) * OFPS)))

    def __iter__(self):
        cap = self.cap
        f0 = int(round(self.s_t * self.sfps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
        src_idx = f0
        ok, cur = cap.read()
        if not ok:
            return
        nxt_ok, nxt = cap.read()
        for j in range(self.n_out):
            t = self.s_t + j / OFPS
            while nxt_ok and (src_idx + 1) / self.sfps <= t + 1e-6:
                cur = nxt
                src_idx += 1
                nxt_ok, nxt = cap.read()
            yield j, cur
        cap.release()


def analyse_segment(src: Path, s_t: float, e_t: float, project: dict[str, Any], stride: int = 2,
                    weights: Path | None = None, progress: Callable[[int, str], None] | None = None) -> dict[str, Any]:
    """Pass 1: subject boxes every `stride` output frames, scene cuts, facecam rect, smoothed crop x0."""
    rd = _FrameReader(src, s_t, e_t)
    W, H, n = rd.W, rd.H, rd.n_out
    model = None
    try:
        from pipeline import get_yolo_model  # lazy: avoids a circular import at module load

        model = get_yolo_model(Path(weights) if weights is not None else None)
    except Exception:
        model = None
    dets: list[list[list[float]] | None] = [None] * n
    cuts_f: list[int] = []
    prev_hist = None
    grays: list[np.ndarray] = []
    gray_every = max(1, n // 15)
    for j, fr in rd:
        small = cv2.resize(fr, (160, 90))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        if prev_hist is not None and cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA) > 0.45:
            if not cuts_f or j - cuts_f[-1] > 15:
                cuts_f.append(j)
        prev_hist = hist
        if j % gray_every == 0:
            grays.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
        if model is not None and j % stride == 0:
            try:
                from device import yolo_device as _yolo_device
                r = model.predict(fr, imgsz=640, classes=[0], conf=0.25, verbose=False, device=_yolo_device())[0]
                dets[j] = [[*map(float, b.xyxy[0].tolist()), float(b.conf[0])] for b in r.boxes]
            except Exception:
                dets[j] = []
        if progress and j % 60 == 0:
            progress(int(100 * j / n), f"Suivi du sujet {j}/{n} images")

    # Facecam: static-person-near-edge + border snap, else the facecam box from 03 / TRACK
    cam = _find_facecam([d for d in dets if d is not None], W, H) if model is not None else None
    cam_src = None
    if cam:
        cam = _refine_facecam(grays, cam, W, H)
        cam_src = "yolo+edges"
    else:
        fb = project.get("face_box") or {}
        if fb.get("detected"):
            cam = [int(fb["x"] * W), int(fb["y"] * H), int(fb["w"] * W), int(fb["h"] * H)]
            cam_src = "blazeface"

    cw = int(round(H * 9 / 16)) // 2 * 2
    cw = min(cw, W // 2 * 2)
    tx, last, miss, prior = [], W / 2, 0, W / 2
    cut_set = set(cuts_f)
    hits = 0
    for j in range(n):
        if j in cut_set:
            last, miss = W / 2, 0
        f = dets[j]
        if f is None:          # not sampled (stride) -> hold
            tx.append(last)
            continue
        cand = [b for b in f if not _inside(b, cam) and (b[3] - b[1]) > 0.08 * H]
        if cand:
            def cost(b, last=last, prior=prior):
                cx = (b[0] + b[2]) / 2
                return abs(cx - last) / W + abs(cx - prior) / W * 0.6 - (b[3] - b[1]) / H * 0.8 - b[4] * 0.3

            b = min(cand, key=cost)
            last, miss = (b[0] + b[2]) / 2, 0
            hits += 1
        else:
            miss += stride
            if miss > OFPS:
                last = last + (W / 2 - last) * 0.03 * stride
        tx.append(last)
    if model is None:
        cx_fallback = float(project.get("crop_x", 0.5)) * W
        tx = [cx_fallback] * n
    sx = _smooth(tx, cuts_f, OFPS, cw)
    x0 = np.clip(sx - cw / 2, 0, W - cw).astype(int)
    sampled = sum(1 for d in dets if d is not None)
    return {"W": W, "H": H, "n": n, "crop_w": cw, "x0": x0.tolist(), "facecam": cam, "facecam_source": cam_src,
            "cuts": [round(c / OFPS, 2) for c in cuts_f], "yolo": model is not None,
            "yolo_hits": hits, "yolo_sampled": sampled}


def render_tracked_video(src: Path, s_t: float, e_t: float, track: dict[str, Any], out_mp4: Path,
                         inset: bool, inset_width: float, progress: Callable[[int, str], None] | None = None) -> None:
    """Pass 2: compose 1080x1920 frames (tracked game crop + facecam inset on top) -> video-only mp4."""
    ffmpeg = get_ffmpeg_path()
    kw: dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000
    errlog = tempfile.TemporaryFile()  # a file, not a pipe: ffmpeg can never block on a full stderr buffer
    ff = subprocess.Popen([ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OW}x{OH}",
                           "-r", str(OFPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                           "-pix_fmt", "yuv420p", str(out_mp4)], stdin=subprocess.PIPE, stderr=errlog, **kw)
    cw, x0s, cam = track["crop_w"], track["x0"], track["facecam"]
    n = track["n"]
    try:
        for j, fr in _FrameReader(src, s_t, e_t):
            x = x0s[min(j, len(x0s) - 1)]
            crop = fr[:, x:x + cw]
            img = cv2.resize(crop, (OW, OH), interpolation=cv2.INTER_CUBIC)
            if inset and cam is not None:
                fx, fy, fw, fh = cam
                face = fr[max(0, fy):fy + fh, max(0, fx):fx + fw]
                if face.size:
                    iw = int(OW * inset_width) // 2 * 2
                    ih = int(iw * face.shape[0] / face.shape[1])
                    face = cv2.resize(face, (iw, ih), interpolation=cv2.INTER_AREA)
                    ox, oy = (OW - iw) // 2, int(OH * 0.06)
                    cv2.rectangle(img, (ox - 6, oy - 6), (ox + iw + 5, oy + ih + 5), (255, 255, 255), -1)
                    img[oy:oy + ih, ox:ox + iw] = face
            try:
                ff.stdin.write(img.tobytes())
            except (BrokenPipeError, OSError):
                break  # ffmpeg exited early: its error is reported below
            if progress and j % 60 == 0:
                progress(int(100 * j / n), f"Compositing 9:16 frame {j}/{n}")
    finally:
        try:
            ff.stdin.close()
        except OSError:
            pass
        ff.wait()
        errlog.seek(0)
        err = errlog.read().decode("utf-8", "replace")
        errlog.close()
    if ff.returncode != 0 or not out_mp4.exists():
        raise RuntimeError(f"Tracked render failed: {err[-400:]}")


def _loudnorm_measure(src: Path, s_t: float, e_t: float, speed: float) -> str:
    ffmpeg = get_ffmpeg_path()
    af = "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json"
    if abs(speed - 1.0) > 0.05:
        from pipeline import _atempo_chain  # lazy: avoids circular import at module load

        af = _atempo_chain(speed) + "," + af
    r = _run([ffmpeg, "-hide_banner", "-ss", f"{s_t:.3f}", "-to", f"{e_t:.3f}", "-i", str(src), "-vn", "-af", af, "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        return "loudnorm=I=-14:TP=-1.5:LRA=11"
    j = json.loads(m.group(0))
    try:
        if not np.isfinite(float(j["input_i"])):
            return "loudnorm=I=-14:TP=-1.5:LRA=11"
    except Exception:
        return "loudnorm=I=-14:TP=-1.5:LRA=11"
    return ("loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={input_i}:measured_TP={input_tp}:measured_LRA={input_lra}:"
            "measured_thresh={input_thresh}:offset={target_offset}:linear=true").format(**j)



def render_tracked_short(project: dict[str, Any], src_video: Path, out_path: Path, s_t: float, e_t: float,
                         opts: dict[str, Any], captions: list[dict[str, Any]] | None = None, speed: float = 1.0,
                         retention: bool = False, sharpen: bool = False, work_dir: Path | None = None,
                         burn_captions: bool = True,
                         progress: Callable[[int, str], None] | None = None) -> dict[str, Any]:
    """Full tracked render. Returns a report dict (track info, caption count, QC)."""
    work_dir = work_dir or out_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    stem = out_path.stem
    tmp_video = work_dir / f"_{stem}_tracked.mp4"
    ass_path = work_dir / f"_{stem}_caps.ass"
    p = progress or (lambda pct, msg: None)
    try:
        p(5, "Suivi du sujet + détection facecam")
        if opts.get("tracked_crop", True):
            track = analyse_segment(src_video, s_t, e_t, project, stride=int(opts.get("yolo_stride", 2)),
                                    progress=lambda pc, m: p(5 + pc * 0.35, m))
        else:
            rd = _FrameReader(src_video, s_t, e_t)
            cw = min(int(round(rd.H * 9 / 16)) // 2 * 2, rd.W // 2 * 2)
            cx = float(project.get("crop_x", 0.5)) * rd.W
            track = {"W": rd.W, "H": rd.H, "n": rd.n_out, "crop_w": cw,
                     "x0": [int(np.clip(cx - cw / 2, 0, rd.W - cw))] * rd.n_out, "facecam": None,
                     "facecam_source": None, "cuts": [], "yolo": False}
            rd.cap.release()
            fb = project.get("face_box") or {}
            if fb.get("detected"):
                track["facecam"] = [int(fb["x"] * rd.W), int(fb["y"] * rd.H), int(fb["w"] * rd.W), int(fb["h"] * rd.H)]
                track["facecam_source"] = "blazeface"
        inset = bool(opts.get("facecam_inset", True))
        p(42, "Compositing facecam inset + 9:16 crop")
        render_tracked_video(src_video, s_t, e_t, track, tmp_video, inset, float(opts.get("inset_width", 0.46)),
                             progress=lambda pc, m: p(42 + pc * 0.33, m))

        n_caps = 0
        if burn_captions and captions:
            n_caps = write_caption_ass(captions, ass_path, s_t, e_t, speed, opts)
        filters = []
        if abs(speed - 1.0) > 0.05:
            filters.append(f"setpts={1.0 / speed:.4f}*PTS")
        if n_caps:
            filters.append(f"ass='{_ffmpeg_esc(ass_path)}':fontsdir='{_ffmpeg_esc(FONTS_DIR)}'")
        filters.append("fps=30")
        graph = "[0:v]" + ",".join(filters) + "[b0]"
        post = []
        out_dur = (e_t - s_t) / speed
        if retention:
            post.append(f"drawbox=x=0:y=1904:w='min(1080,1080*t/{out_dur:.3f})':h=16:color=#FF2D55@0.95:t=fill")
        if sharpen:
            post.append("unsharp=5:5:0.8:3:3:0.4")
        post.append("format=yuv420p")
        graph += ";[b0]" + ",".join(post) + "[outv]"

        p(80, "2-pass loudnorm -14 LUFS")
        ln = _loudnorm_measure(src_video, s_t, e_t, speed)
        af = []
        if abs(speed - 1.0) > 0.05:
            from pipeline import _atempo_chain

            af.append(_atempo_chain(speed))
        af += [ln, "aresample=48000"]
        ffmpeg = get_ffmpeg_path()
        cmd = [ffmpeg, "-y", "-hide_banner", "-i", str(tmp_video), "-ss", f"{s_t:.3f}", "-to", f"{e_t:.3f}", "-i", str(src_video)]
        cmd += ["-filter_complex", graph, "-map", "[outv]", "-map", "1:a:0?", "-af", ",".join(af),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out_path)]
        p(85, "Burning captions + final encode")
        r = _run(cmd)
        if (r.returncode != 0 or not out_path.exists()) and n_caps:
            # libass/font failure: retry without captions rather than fail the export
            graph2 = graph.replace(f"ass='{_ffmpeg_esc(ass_path)}':fontsdir='{_ffmpeg_esc(FONTS_DIR)}',", "")
            cmd[cmd.index("-filter_complex") + 1] = graph2
            r = _run(cmd)
            n_caps = 0
        if r.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"Tracked render failed: {r.stderr[-600:]}")
        p(95, "QC (1080x1920 / 30 fps / -14 LUFS / freeze / black)")
        qc = qc_report(out_path, opts.get("min_duration"), opts.get("max_duration"))
        return {
            "facecam": track.get("facecam"), "facecam_source": track.get("facecam_source"),
            "yolo": track.get("yolo"), "yolo_hits": track.get("yolo_hits"), "yolo_sampled": track.get("yolo_sampled"),
            "scene_cuts": track.get("cuts"), "caption_pops": n_caps, "captions_burned": bool(n_caps),
            "inset": bool(inset and track.get("facecam")), "qc": qc,
        }
    finally:
        for f in (tmp_video, ass_path):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass
