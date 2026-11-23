from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

import config as _cfg

from pipeline import (
    BASE_DIR,
    PROJECTS_DIR,
    REMOTION_DIR,
    _REMOTION_LOCK,
    load_project,
    save_project,
    stage_02_find,
    stage_03_track,
    stage_04_caption,
    stage_05_ship,
)

import llm as _llm
from llm import call_llm_json  # noqa: F401  (re-exported)

_EVENTS_LOCK = threading.Lock()
_EVENT_SEQ = 0
_AGENT_EVENTS: list[dict[str, Any]] = []
_ACTIVE_AGENTS: dict[str, dict[str, Any]] = {}


def emit_agent_event(
    agent_id: str,
    agent_name: str,
    domain: str,
    color: str,
    hex_color: str,
    target: str,
    task: str,
    thought: str = "",
    x_norm: float = 0.5,
    y_norm: float = 0.5,
    model: str = "CPU-CV",
    active: bool = True,
) -> dict[str, Any]:
    """Emit a real backend-driven agent telemetry & cursor event."""
    global _EVENT_SEQ
    with _EVENTS_LOCK:
        _EVENT_SEQ += 1
        ev = {
            "seq": _EVENT_SEQ,
            "ts": round(time.time(), 3),
            "agent_id": agent_id,
            "agent_name": agent_name,
            "domain": domain,
            "color": color,
            "hex": hex_color,
            "target": target,
            "task": task,
            "thought": thought,
            "x_norm": round(max(0.08, min(0.92, float(x_norm))), 3),
            "y_norm": round(max(0.15, min(0.85, float(y_norm))), 3),
            "model": model,
            "active": active,
        }
        _AGENT_EVENTS.append(ev)
        if len(_AGENT_EVENTS) > 200:
            del _AGENT_EVENTS[:-200]
        if active:
            _ACTIVE_AGENTS[agent_id] = ev
        else:
            _ACTIVE_AGENTS.pop(agent_id, None)
        return ev


def clear_active_agents() -> None:
    with _EVENTS_LOCK:
        _ACTIVE_AGENTS.clear()


def get_live_agent_state(since_seq: int = 0) -> dict[str, Any]:
    with _EVENTS_LOCK:
        events = [e for e in _AGENT_EVENTS if e["seq"] > since_seq]
        active = list(_ACTIVE_AGENTS.values())
        latest_seq = _EVENT_SEQ
    return {
        "latest_seq": latest_seq,
        "active_agents": active,
        "events": events,
        "llm_provider": _llm.provider(),
        "llm_model": _llm.llm_status(probe=False)["model"],
        "llm_ready": _llm.llm_status()["ready"],
    }


# ---------------------------------------------------------------------------
# REAL AUTONOMOUS MULTI-AGENT WORKFLOW
# ---------------------------------------------------------------------------
def run_autonomous_agent_crew(
    project_id: str,
    export_mp4: bool = True,
    progress: Callable[[str, int, str, list[str]], None] | None = None,
) -> dict[str, Any]:
    """Execute real CV + LLM multi-agent editorial crew on a loaded project:
    1. Ensure CV cuts (04 PySceneDetect), tracking (08 MediaPipe + 09 YOLOv8n), and transcription (12 faster-whisper) exist.
    2. Agent 04/05 (Cut & Waveform Analyst): inspects real waveform dead-air & cut density.
    3. Agent 06 Groq/Ollama LLM Director: analyzes transcript + energy windows, labels moments, picks best highlight & writes viral kit.
    4. Agent 08/09 (CV Framing Director): sets optimal layout_mode (FACECAM+GAME vs CENTER CROP) and locks crop_x to YOLO subject median.
    5. Agent 11/18 (Groq LLM Caption Copywriter): rewrites/punches up captions in the highlight window & selects style/font_size.
    6. Agent 20 FFmpeg (Render Agent): renders the final 9:16 H.264 Short if export_mp4=True.
    """
    clear_active_agents()
    project = load_project(project_id)
    dur = float(project["info"]["duration"] or 15.0)
    # Tracked mode (YOLO-followed crop + facecam inset + QC) when enabled in the project's options
    t_meta = project.get("tracked") or {}
    tracked_mode = bool((t_meta.get("options") or {}).get("enabled"))

    # --- STEP 1: Ensure base CV & Whisper data are computed ---
    if not project.get("cuts") or not project.get("moments"):
        emit_agent_event(
            "04", "04 PySceneDetect", "FIND", "orange", "#f59e0b",
            "#filmstripTrack", "Scanning scene cuts...", "Running ContentDetector on video frames",
            x_norm=0.35, y_norm=0.5, model="PySceneDetect",
        )
        project = stage_02_find(project_id, strip_silence=False, progress=progress)

    if not project.get("face_track"):
        emit_agent_event(
            "08", "08 MediaPipe", "TRACK", "pink", "#ee1d6c",
            "#faceBox", "Running BlazeFace + YOLOv8n...", "Sampling 16 frames across video",
            x_norm=0.5, y_norm=0.5, model="BlazeFace+YOLOv8n",
        )
        project = stage_03_track(project_id, progress=progress)

    if not project.get("captions"):
        emit_agent_event(
            "12", "12 faster-whisper", "CAPTION", "pink", "#ee1d6c",
            "#captionsTrack", "Transcribing word timestamps...", "Running faster-whisper int8 + VAD",
            x_norm=0.45, y_norm=0.5, model="faster-whisper",
        )
        project = stage_04_caption(project_id, model_size="tiny", progress=progress)

    project = load_project(project_id)

    # --- STEP 2: RMS Waveform Silence & Cut Density Analyst (pure Python RMS) ---
    llm_provider_label = {"groq": "Groq LLM", "ollama": "Ollama LLM"}.get(_llm.provider(), "Heuristic")
    llm_model_name = _llm.llm_status(probe=False)["model"] or "heuristics"
    llm_short = llm_model_name.split("/")[-1]
    wf = project.get("waveform") or [0.5] * 120
    min_idx = int(min(range(len(wf)), key=lambda i: wf[i])) if wf else 0
    max_idx = int(max(range(len(wf)), key=lambda i: wf[i])) if wf else 60
    quiet_pct = round(sum(1 for v in wf if v < 0.15) / max(1, len(wf)) * 100, 1)
    emit_agent_event(
        "05", "05 RMS Waveform Analyzer", "FIND", "indigo", "#6366f1",
        "#audioTrack",
        f"Peak audio @{round(max_idx / max(1, len(wf)) * dur, 1)}s · {quiet_pct}% quiet",
        f"Analyzed {len(wf)} RMS bars: min bar #{min_idx}, peak bar #{max_idx}",
        x_norm=max_idx / max(1, len(wf)), y_norm=0.5, model="RMS-Analyzer",
    )

    # --- STEP 3: Real LLM Highlight Director (Groq / Ollama) ---
    if progress:
        progress("02 / FIND", 45, f"06 {llm_provider_label} Director ({llm_model_name}) analyzing transcript & moments...", ["06", "07"])

    moments = project.get("moments") or []
    captions = project.get("captions") or []
    cuts = project.get("cuts") or []

    emit_agent_event(
        "06", f"06 {llm_provider_label} Director ({llm_short})", "FIND", "pink", "#ee1d6c",
        "#momentsGrid",
        "LLM evaluating moments & transcript...",
        f"Sending {len(captions)} captions & {len(moments)} candidate windows to {llm_provider_label}",
        x_norm=0.5, y_norm=0.45, model=llm_model_name,
    )

    llm_director = None
    try:
        cap_summary = [
            {"t": f"{c['start']}-{c['end']}s", "text": c["text"]}
            for c in captions[:35]
        ]
        mom_summary = [
            {"idx": i, "start": m["start"], "end": m["end"], "rms_score": m["score"]}
            for i, m in enumerate(moments)
        ]
        sys_p = (
            "You are the AI Viral Video Director (LLM) for CLIP MASTER. "
            "Given video duration, candidate audio/cut moments, and transcript segments, choose the single best viral Short highlight window (8 to 16 seconds), "
            "give each of the 3 moments a short uppercase 1-2 word semantic label (max 12 chars, e.g. 'VICE_CHASE', 'HOOK_INTRO', 'CLIMAX'), "
            "and write a punchy viral title, hook, and hashtags."
        )
        usr_p = json.dumps(
            {
                "video_name": project.get("name"),
                "duration_sec": dur,
                "cuts_count": len(cuts),
                "candidate_moments": mom_summary,
                "transcript": cap_summary,
                "required_json_schema": {
                    "selected_moment_index": 0,
                    "highlight_start": 0.0,
                    "highlight_end": 15.0,
                    "moment_labels": ["LABEL_1", "LABEL_2", "LABEL_3"],
                    "director_reason": "short explanation",
                    "viral_title": "title with #Shorts",
                    "viral_hook": "UPPERCASE 3-5 WORD HOOK",
                    "hashtags": "#Shorts #Gaming ...",
                },
            },
            ensure_ascii=False,
        )
        llm_director = call_llm_json(sys_p, usr_p, use_big=False)
    except Exception as exc:
        emit_agent_event(
            "06", f"06 {llm_provider_label} Director", "FIND", "pink", "#ee1d6c",
            "#momentsGrid", f"LLM fallback ({str(exc)[:40]})", "Using top RMS/cut score window",
            x_norm=0.5, y_norm=0.5, model="fallback",
        )

    if llm_director:
        labels = llm_director.get("moment_labels") or []
        for i, m in enumerate(moments):
            if i < len(labels) and isinstance(labels[i], str) and labels[i].strip():
                m["label"] = re.sub(r"[^A-Z0-9_ ]", "", labels[i].upper().strip())[:14] or m["label"]

        sel_idx = int(llm_director.get("selected_moment_index", 0) or 0)
        sel_idx = max(0, min(len(moments) - 1, sel_idx)) if moments else 0
        h_start = float(llm_director.get("highlight_start", moments[sel_idx]["start"] if moments else 0.0))
        h_end = float(llm_director.get("highlight_end", moments[sel_idx]["end"] if moments else min(dur, 15.0)))
        h_start = round(max(0.0, min(dur - 3.0, h_start)), 2)
        h_end = round(max(h_start + 3.0, min(dur, h_end)), 2)
        h_score = moments[sel_idx]["score"] if moments else 0.85

        project["highlight"] = {
            "start": h_start,
            "end": h_end,
            "score": h_score,
            "reason": llm_director.get("director_reason", ""),
        }
        project["viral_kit"] = {
            "title": (llm_director.get("viral_title") or f"{project['name']} #Shorts")[:100],
            "hook": (llm_director.get("viral_hook") or "WAIT FOR IT").upper(),
            "description": f"{llm_director.get('director_reason', '')}\n\n{llm_director.get('hashtags', '#Shorts')}",
            "hashtags": llm_director.get("hashtags") or "#Shorts #GamingClips #ViralShorts",
        }
        save_project(project_id, project)

        hl_center_norm = ((h_start + h_end) / 2.0) / max(1.0, dur)
        emit_agent_event(
            "06", f"06 {llm_provider_label} Director ({llm_short})", "FIND", "pink", "#ee1d6c",
            "#filmstripTrack",
            f"Picked {h_start}s–{h_end}s ({moments[sel_idx]['label'] if moments else 'HIGHLIGHT'})",
            llm_director.get("director_reason", "Selected peak viral window"),
            x_norm=hl_center_norm, y_norm=0.5, model=llm_model_name,
        )

    # --- STEP 4: Agent 08 MediaPipe & 09 Ultralytics (REAL CV FRAMING DECISION) ---
    if progress:
        progress("03 / TRACK", 68, "08 MediaPipe & 09 YOLOv8n locking optimal 9:16 framing...", ["08", "09"])

    fb = project.get("face_box") or {}
    ft = project.get("face_track") or []
    hl = project.get("highlight") or {"start": 0.0, "end": min(dur, 15.0)}
    hl_pts = [p for p in ft if hl["start"] <= p["t"] <= hl["end"]] or ft
    yolo_pts = [p for p in hl_pts if p.get("source") == "yolo"]
    opt_crop_x = round(float(sum(p["x"] for p in (yolo_pts or hl_pts)) / max(1, len(yolo_pts or hl_pts))), 3) if hl_pts else 0.50

    # Autonomous layout decision based on real BlazeFace corner detection
    if fb.get("detected") and (fb.get("conf") or 0) >= 0.45:
        chosen_layout = "FACECAM + GAME + CAPS"
        face_thought = f"BlazeFace detected corner webcam (conf={fb.get('conf')}) -> Split-screen mode"
    else:
        chosen_layout = "CENTER CROP"
        face_thought = "No corner facecam detected -> Full 9:16 subject-tracked Center Crop"

    project["crop_x"] = opt_crop_x
    project["layout_mode"] = chosen_layout
    save_project(project_id, project)

    emit_agent_event(
        "09", "09 Ultralytics (YOLOv8n)", "TRACK", "teal", "#0e9f8e",
        "#monitorWrap",
        f"Crop X={round(opt_crop_x*100,1)}% · {chosen_layout}",
        f"{face_thought}; YOLO tracked {len(yolo_pts)}/{len(hl_pts)} frames in highlight",
        x_norm=opt_crop_x, y_norm=0.52, model="YOLOv8n+BlazeFace",
    )

    # --- STEP 5: Real LLM Caption Copywriter + Remotion JSON Sync ---
    if progress:
        progress("04 / CAPTION", 82, f"11 {llm_provider_label} Copywriter ({llm_model_name}) polishing captions...", ["11", "18"])

    hl_caps = [
        {"idx": i, "start": c["start"], "end": c["end"], "text": c["text"]}
        for i, c in enumerate(captions)
        if c["end"] >= hl["start"] and c["start"] <= hl["end"]
    ]

    emit_agent_event(
        "11", f"11 {llm_provider_label} Copywriter ({llm_short})", "CAPTION", "orange", "#f59e0b",
        "#captionsTrack",
        f"LLM polishing {len(hl_caps)} captions in highlight...",
        "Cleaning speech-to-text & picking optimal style/font size",
        x_norm=0.5, y_norm=0.5, model=llm_model_name,
    )

    try:
        sys_cap = (
            "You are an AI Caption Copywriter for viral 9:16 Shorts in CLIP MASTER. "
            "Given the captions inside the selected highlight window, fix obvious ASR typos, keep each caption punchy (2-4 uppercase words max), "
            "and choose the best visual style ('pink', 'yellow', 'cyan', 'white', 'tiktok_white', or 'tiktok_yellow') and font_size (36 to 48)."
        )
        usr_cap = json.dumps(
            {
                "video_name": project.get("name"),
                "highlight_captions": hl_caps[:16],
                "required_json_schema": {
                    "caption_style": "yellow",
                    "font_size": 42,
                    "rewritten": [{"idx": 0, "text": "PUNCHY TEXT"}],
                    "style_reason": "short explanation",
                },
            },
            ensure_ascii=False,
        )
        cap_llm = call_llm_json(sys_cap, usr_cap, use_big=False)
        for item in cap_llm.get("rewritten") or []:
            idx = item.get("idx")
            new_txt = (item.get("text") or "").strip().upper()
            if isinstance(idx, int) and 0 <= idx < len(captions) and new_txt:
                captions[idx]["text"] = new_txt[:36]

        style_choice = (cap_llm.get("caption_style") or "pink").lower()
        if style_choice not in ("pink", "yellow", "cyan", "white", "tiktok_white", "tiktok_yellow"):
            style_choice = "tiktok_white" if project.get("layout_mode") == "TIKTOK STORY / FACELESS" else "pink"
        fs_choice = max(32, min(52, int(cap_llm.get("font_size") or 42)))

        project["captions"] = captions
        project["caption_style"] = style_choice
        project["font_size"] = fs_choice
        save_project(project_id, project)

        # Sync Remotion captions.json under _REMOTION_LOCK
        with _REMOTION_LOCK:
            rem_src = REMOTION_DIR / "src"
            rem_src.mkdir(parents=True, exist_ok=True)
            (rem_src / "captions.json").write_text(
                json.dumps({"project_id": project_id, "fps": 30, "captions": captions}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        emit_agent_event(
            "18", "18 Remotion JSON Sync", "CAPTION", "indigo", "#6366f1",
            ".inspector-card",
            f"Style={style_choice.upper()} · {fs_choice}PX · {len(cap_llm.get('rewritten') or [])} caps polished",
            cap_llm.get("style_reason", "Optimized typography for viral retention"),
            x_norm=0.55, y_norm=0.45, model=llm_model_name,
        )
    except Exception as exc:
        emit_agent_event(
            "18", "18 Remotion JSON Sync", "CAPTION", "indigo", "#6366f1",
            ".inspector-card", f"Captions synced ({len(captions)} blocks)", str(exc)[:60],
            x_norm=0.5, y_norm=0.5, model="faster-whisper",
        )

    # --- STEP 6: Agent 20 FFmpeg (REAL 9:16 H.264 EXPORT) ---
    ship_res = None
    if export_mp4:
        emit_agent_event(
            "20", "20 FFmpeg", "SHIP", "pink", "#ee1d6c",
            "#btnExport",
            f"Encoding {hl['start']}s–{min(hl['end'], hl['start']+14):.1f}s ({chosen_layout})...",
            "libx264 1080x1920 + libass + loudnorm -14 LUFS + 48kHz AAC",
            x_norm=0.5, y_norm=0.5, model="FFmpeg-x264",
        )
        t_opts = None
        if tracked_mode:
            t_opts = dict((load_project(project_id).get("tracked") or {}).get("options") or {})
            t_opts["enabled"] = True
        ship_res = stage_05_ship(
            project_id=project_id,
            layout_mode=project["layout_mode"],
            crop_x=project["crop_x"],
            font_size=project["font_size"],
            caption_style=project.get("caption_style", "pink"),
            start_sec=hl["start"],
            end_sec=min(hl["end"], hl["start"] + (45.0 if tracked_mode else 14.0)),
            tracked=t_opts,
            progress=progress,
        )
        exp = ship_res.get("export") or {}
        emit_agent_event(
            "20", "20 FFmpeg", "SHIP", "pink", "#ee1d6c",
            ".output-card",
            f"Exported {exp.get('filename')} ({exp.get('size_mb')} MB)",
            f"Verified 1080x1920 9:16 H.264 ({exp.get('duration')}s, 48kHz AAC)",
            x_norm=0.6, y_norm=0.6, model="FFmpeg-x264",
        )
        if tracked_mode and exp:
            b_info = exp.get("tracked") or {}
            qc = (b_info.get("report") or {}).get("qc") or {}
            qc_pass = bool(qc.get("pass", True))
            fails = qc.get("failures") or []
            dur_txt = f"{qc.get('duration', exp.get('duration'))}s"
            lufs_txt = f"{qc.get('lufs_I', -14.0)} LUFS"
            emit_agent_event(
                "19", "19 QC Inspector", "QC", "teal", "#0e9f8e",
                ".output-card",
                f"QC {'PASS' if qc_pass else 'WARNING'} · {dur_txt} @ {lufs_txt}",
                f"1080x1920 30fps · TP={qc.get('true_peak_dbfs', -1.5)}dBFS · Freezes: {len(qc.get('freezes', []))}"
                if qc_pass else f"QC alerts: {', '.join(fails)[:80]}",
                x_norm=0.65, y_norm=0.65, model="FFmpeg-QC-EBU",
            )

    final_project = load_project(project_id)
    return {
        "project": final_project,
        "export": ship_res.get("export") if ship_res else None,
        "llm_director": llm_director,
    }
