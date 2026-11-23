"""Stage 05 / SHIP: optional voice split, stream-copy trim, 9:16 encode, tracked-mode export.

First step of splitting the old ``pipeline.py`` monolith. ``pipeline`` re-exports every public name of this
module (``stage_05_ship``, ``_render_vertical``, ``_build_vertical_filter``, ``_atempo_chain``,
``_ffmpeg_path_escape``), so ``import pipeline; pipeline.stage_05_ship(...)`` keeps working unchanged.

Shared helpers (project load/save, ffprobe, subprocess runner, ASS writer, folders) still live in
``pipeline``; they are looked up on the module at call time so tests can patch them there.
Slow-mo uses motion-compensated frame interpolation + atempo; sharpen uses an unsharp filter.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import presets as _presets
import tracked_render
from messages import LocalizedRuntimeError
from tools_registry import get_ffmpeg_path

logger = logging.getLogger("clip_master.ship")

ProgressCallback = Callable[[str, int, str, list[str]], None]


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
    import pipeline as pl

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
    return pl._run_cmd(cmd, timeout=900)


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
    export_preset: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    import pipeline as pl  # lazy: pipeline re-exports this module, so a top-level import would be circular

    _presets.get_preset(export_preset)  # fail early on a typo
    project = pl.load_project(project_id)
    pdir = pl.PROJECTS_DIR / project_id
    video_path = pdir / project["video_file"]
    ffmpeg = get_ffmpeg_path()
    active_repos = ["14", "19", "20"]
    # Tracked mode (followed crop + facecam inset): explicit request options win, else the project's saved options
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

    # Export preset (TikTok / Shorts / Reels): cap the length, keep captions in the platform safe zone
    s_t, e_t, preset_trimmed = _presets.clamp_window(export_preset, s_t, e_t, speed)
    caption_style = _presets.resolve_caption_style(export_preset, caption_style)
    preset_margin_v = _presets.caption_margin_v(export_preset)
    if export_preset:
        t_opts["caption_ypos"] = min(float(t_opts["caption_ypos"]), _presets.caption_ypos(export_preset))
        if progress and preset_trimmed:
            progress("05 / SHIP", 12, f"Preset {export_preset}: durée limitée à {_presets.PRESETS[export_preset]['max_duration']} s", ["20"])

    if t_opts["enabled"]:
        use_demucs = False      # tracked mode keeps the original audio
        lossless_trim = False   # frame-accurate cut needed

    temp_files: list[Path] = []

    # Stream-copy trim (keyframe-accurate, no re-encode)
    working_video = video_path
    seek = True
    if lossless_trim and (s_t > 0.1 or e_t < dur - 0.1):
        if progress:
            progress("05 / SHIP", 20, "Trim sans ré-encodage (copie de flux)…", ["20"])
        trimmed = pdir / "trimmed_lossless.mp4"
        res = pl._run_cmd([ffmpeg, "-y", "-ss", f"{s_t:.3f}", "-to", f"{e_t:.3f}", "-i", str(video_path), "-c", "copy", str(trimmed)])
        if res.returncode == 0 and trimmed.exists():
            working_video = trimmed
            temp_files.append(trimmed)
            seek = False
            # stream copy snaps to the previous keyframe: measure the real resulting duration
            real = pl.get_video_info(trimmed)["duration"] or (e_t - s_t)
            e_t = s_t + real

    # Optional voice isolation (runs on the working clip)
    audio_override = None
    if use_demucs:
        if progress:
            progress("05 / SHIP", 35, "Séparation de la voix et de la musique (peut être long)…", ["14"])
        demucs_out = pdir / "demucs_out"
        res = pl._run_cmd(
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
    pl._write_ass_subtitles(shifted_caps, ass_path, font_size=font_size, split_mode=split_mode, caption_style=caption_style,
                         min_margin_v=preset_margin_v)

    out_name = f"{'TRACKED_' if t_opts['enabled'] else ''}{short_label}_{project_id}_{int(time.time())}.mp4"
    out_path = pl.OUTPUTS_DIR / out_name
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
        out_info = pl.get_video_info(out_path)
        project = pl.load_project(project_id)
        project.setdefault("tracked", {})["options"] = t_opts
        kit = pl.generate_viral_kit(project)
        qc = report.get("qc") or {}
        qc_ok = bool(qc.get("pass"))
        qc_failures = qc.get("failures") or []
        rejected = bool(t_opts["strict"] and not qc_ok)
        if rejected and out_path.exists():
            # Keep the file, but flag it so it is not treated as validated
            rejected_name = f"REJECTED_{out_name}"
            rejected_path = pl.OUTPUTS_DIR / rejected_name
            try:
                os.replace(out_path, rejected_path)
                out_name = rejected_name
                out_path = rejected_path
                out_info = pl.get_video_info(out_path)
            except OSError:
                pass
        export_entry = {
            "filename": out_name,
            "layout_mode": "TRACKED 9:16",
            "size_mb": round(out_path.stat().st_size / (1024 * 1024), 2) if out_path.exists() else 0,
            "duration": out_info["duration"],
            "speed": speed,
            "captions_burned": report["captions_burned"],
            "preset": export_preset,
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
        pl.save_project(project_id, project)
        if rejected:
            why = " ; ".join(qc_failures) or "QC non conforme"
            raise LocalizedRuntimeError("pipeline.qc_failed", why=why, name=out_name)
        if progress:
            flag = "QC OK" if qc_ok else "QC à vérifier"
            progress("05 / SHIP", 100, f"TRACKED READY — {out_name} ({qc.get('width')}x{qc.get('height')}, {qc.get('duration')}s, {qc.get('lufs_I')} LUFS, {flag})", ["20"])
        return {"project": project, "export": export_entry}

    if progress:
        msg = f"Encodage 9:16 H.264 ({(e_t - s_t) / speed:.1f}s"
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
            raise LocalizedRuntimeError("pipeline.export_failed", err=res.stderr[-500:])

    for tf in temp_files:
        try:
            if tf.is_dir():
                shutil.rmtree(tf, ignore_errors=True)
            else:
                tf.unlink(missing_ok=True)
        except Exception:
            logger.warning("Could not remove temporary file %s", tf, exc_info=True)

    out_info = pl.get_video_info(out_path)
    export_entry = {
        "filename": out_name,
        "layout_mode": layout_mode,
        "size_mb": round(out_path.stat().st_size / (1024 * 1024), 2),
        "duration": out_info["duration"],
        "speed": speed,
        "captions_burned": captions_burned,
        "preset": export_preset,
        "created_at": int(time.time()),
    }
    project = pl.load_project(project_id)  # reload: UI may have saved edits meanwhile
    project.setdefault("exports", []).insert(0, export_entry)
    project["layout_mode"] = layout_mode
    project["crop_x"] = cx
    project["font_size"] = font_size
    project["caption_style"] = caption_style
    project["viral_kit"] = pl.generate_viral_kit(project)
    if "ship" not in project["stage_completed"]:
        project["stage_completed"].append("ship")
    pl.save_project(project_id, project)

    if progress:
        warn = "" if captions_burned or not shifted_caps else " (sans sous-titres: échec libass)"
        progress("05 / SHIP", 100, f"READY TO POST — {out_name} ({export_entry['size_mb']} MB, {out_info['duration']}s){warn}", active_repos)
    return {"project": project, "export": export_entry}
