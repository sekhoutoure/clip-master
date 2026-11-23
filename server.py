from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from agents_crew import (
    clear_active_agents,
    emit_agent_event,
    get_live_agent_state,
    run_autonomous_agent_crew,
)
from pipeline import (
    OUTPUTS_DIR,
    PROJECTS_DIR,
    _project_lock,
    project_run_lock,
    generate_viral_kit,
    load_project,
    save_project,
    stage_01_pull,
    stage_02_find,
    stage_03_track,
    stage_04_caption,
    stage_05_batch_ship,
    stage_05_ship,
    stage_remotion_render,
    validate_project_id,
)
import tracked_render
from config import BASE_DIR, HOST, MAX_UPLOAD_MB, PORT
import llm
from tools_registry import check_tools_status

STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="CLIP MASTER — 9:16 clip studio")

ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}", f"http://{HOST}:{PORT}"}
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024  # hard quota, set CLIP_MASTER_MAX_UPLOAD_MB in .env
MAX_CONCURRENT_JOBS = 1

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def csrf_origin_guard(request: Request, call_next):
    """Reject cross-site and headerless mutating requests (strict anti-CSRF Origin/Referer check)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = (request.headers.get("origin") or "").rstrip("/")
        referer = request.headers.get("referer") or ""
        if origin:
            if origin not in ALLOWED_ORIGINS:
                return JSONResponse({"detail": "Cross-site origin rejected (CSRF protection)"}, status_code=403)
        elif referer:
            if not any(referer.startswith(o + "/") or referer == o for o in ALLOWED_ORIGINS):
                return JSONResponse({"detail": "Cross-site referer rejected (CSRF protection)"}, status_code=403)
        else:
            return JSONResponse({"detail": "Missing Origin/Referer header (CSRF protection)"}, status_code=403)
    return await call_next(request)

JOBS_FILE = PROJECTS_DIR / "_jobs.json"
_JOBS_LOCK = threading.RLock()


def _load_jobs() -> dict[str, dict[str, Any]]:
    if JOBS_FILE.exists():
        try:
            data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
            for j in data.values():
                if j.get("status") == "running":
                    j["status"] = "error"
                    j["error"] = "Serveur redémarré pendant l'exécution"
            return data
        except Exception:
            return {}
    return {}


_LAST_PERSIST = 0.0


def _persist_jobs(force: bool = True) -> None:
    """Write the jobs file. Progress ticks pass force=False and are throttled to one write per 2 s."""
    global _LAST_PERSIST
    with _JOBS_LOCK:
        if not force and time.time() - _LAST_PERSIST < 2.0:
            return
        _LAST_PERSIST = time.time()
        try:
            # Keep last 60 jobs
            items = sorted(JOBS.items(), key=lambda kv: kv[1].get("updated_at", 0), reverse=True)[:60]
            tmp = PROJECTS_DIR / "_jobs.json.tmp"
            tmp.write_text(json.dumps(dict(items), ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, JOBS_FILE)
        except Exception:
            pass


JOBS: dict[str, dict[str, Any]] = _load_jobs()


def _create_job(kind: str) -> str:
    with _JOBS_LOCK:
        running = sum(1 for j in JOBS.values() if j.get("status") == "running")
        if running >= MAX_CONCURRENT_JOBS:
            raise HTTPException(
                status_code=429,
                detail=f"Trop de tâches en cours ({running}/{MAX_CONCURRENT_JOBS}). Attends la fin d'un traitement.",
            )
        jid = uuid.uuid4().hex[:10]
        JOBS[jid] = {
            "id": jid,
            "kind": kind,
            "status": "running",
            "stage": "01 / PULL",
            "percent": 0,
            "message": "Starting...",
            "active_repos": [],
            "result": None,
            "error": None,
            "updated_at": time.time(),
        }
        _persist_jobs()
        return jid


_REPO_AGENT_META: dict[str, tuple[str, str, str, str, str]] = {
    "01": ("01 yt-dlp", "PULL", "pink", "#ee1d6c", ".ingest-bar"),
    "02": ("02 streamlink", "PULL", "indigo", "#6366f1", "#monitorWrap"),
    "03": ("03 TwitchDownloader", "PULL", "teal", "#0e9f8e", ".bin-card"),
    "04": ("04 PySceneDetect", "FIND", "orange", "#f59e0b", "#filmstripTrack"),
    "05": ("05 auto-editor", "FIND", "indigo", "#6366f1", "#audioTrack"),
    "06": ("06 LLM (Highlight Director)", "FIND", "pink", "#ee1d6c", "#momentsGrid"),
    "07": ("07 Window Picker (pipeline.py)", "FIND", "pink", "#ee1d6c", "#highlightBox"),
    "08": ("08 MediaPipe (BlazeFace)", "TRACK", "pink", "#ee1d6c", "#faceBox"),
    "09": ("09 Ultralytics (YOLOv8n)", "TRACK", "teal", "#0e9f8e", "#cropBox"),
    "11": ("11 LLM (Caption Copywriter)", "CAPTION", "orange", "#f59e0b", "#captionInput"),
    "12": ("12 faster-whisper", "CAPTION", "pink", "#ee1d6c", "#captionsTrack"),
    "13": ("13 FFmpeg libass (SRT/ASS)", "CAPTION", "teal", "#0e9f8e", ".inspector-card"),
    "14": ("14 Demucs", "SHIP", "orange", "#f59e0b", "#audioTrack"),
    "18": ("18 Remotion", "CAPTION", "indigo", "#6366f1", ".inspector-card"),
    "19": ("19 FFmpeg Stream-Copy", "SHIP", "teal", "#0e9f8e", "#filmstripTrack"),
    "20": ("20 FFmpeg", "SHIP", "pink", "#ee1d6c", "#btnExport"),
}


def _update_job(jid: str, stage: str, percent: int, message: str, active_repos: list[str]) -> None:
    repos = list(active_repos or [])
    with _JOBS_LOCK:
        if jid not in JOBS:
            return
        JOBS[jid].update(
            {
                "stage": stage,
                "percent": percent,
                "message": message,
                "active_repos": repos,
                "updated_at": time.time(),
            }
        )
        _persist_jobs(force=False)
    # Drive real backend agent cursor events from live pipeline progress (outside the jobs lock)
    x_prog = max(0.15, min(0.85, (percent or 20) / 100.0))
    for i, rid in enumerate(repos[:3]):
        meta = _REPO_AGENT_META.get(rid)
        if meta:
            name, dom, col, hx, tgt = meta
            emit_agent_event(
                rid, name, dom, col, hx, tgt,
                task=message[:56],
                thought=f"Job {jid} · {stage} ({percent}%)",
                x_norm=min(0.88, x_prog + i * 0.12),
                y_norm=0.45 + (i % 2) * 0.15,
                model="pipeline",
            )


def _run_bg(jid: str, fn, *args, **kwargs) -> None:
    target_pid = kwargs.get("project_id")

    def worker():
        try:
            if target_pid:
                validate_project_id(target_pid)
                with project_run_lock(target_pid):
                    res = fn(*args, progress=lambda s, p, m, r: _update_job(jid, s, p, m, r), **kwargs)
            else:
                res = fn(*args, progress=lambda s, p, m, r: _update_job(jid, s, p, m, r), **kwargs)
            with _JOBS_LOCK:
                JOBS[jid]["status"] = "done"
                JOBS[jid]["percent"] = 100
                JOBS[jid]["result"] = res
                JOBS[jid]["updated_at"] = time.time()
                _persist_jobs()
        except Exception as exc:
            with _JOBS_LOCK:
                JOBS[jid]["status"] = "error"
                JOBS[jid]["error"] = str(exc)
                JOBS[jid]["message"] = f"Error: {exc}"
                JOBS[jid]["updated_at"] = time.time()
                _persist_jobs()
        finally:
            threading.Timer(4.0, clear_active_agents).start()

    t = threading.Thread(target=worker, daemon=True)
    t.start()


class PullRequest(BaseModel):
    url: str = Field(max_length=2048)
    source_type: Literal["ytdlp", "streamlink", "twitchdownloader"] = "ytdlp"
    title: str | None = Field(default=None, max_length=120)
    clip_start: str | None = Field(default=None, max_length=16)
    clip_end: str | None = Field(default=None, max_length=16)
    record_seconds: int = Field(default=25, ge=5, le=120)


class StageFindRequest(BaseModel):
    project_id: str
    strip_silence: bool = False


class StageTrackRequest(BaseModel):
    project_id: str


class StageCaptionRequest(BaseModel):
    project_id: str
    model_size: Literal["tiny", "base", "small", "medium"] = "tiny"
    language: str = Field(default="auto", min_length=2, max_length=8)


class StageShipRequest(BaseModel):
    project_id: str
    layout_mode: str = Field(default="FACECAM + GAME + CAPS", max_length=40)
    crop_x: float = Field(default=0.50, ge=0.05, le=0.95)
    font_size: int = Field(default=40, ge=16, le=96)
    caption_style: Literal["pink", "yellow", "cyan", "white", "tiktok_white", "tiktok_yellow"] = "pink"
    use_demucs: bool = False
    lossless_trim: bool = False
    playback_speed: float = Field(default=1.0, ge=0.25, le=2.0)
    upscale_sharpen: bool = False
    retention_bar: bool = True
    normalize_audio: bool = True
    start_sec: float | None = Field(default=None, ge=0.0, le=86400.0)
    end_sec: float | None = Field(default=None, ge=0.0, le=86400.0)
    tracked: dict[str, Any] | None = None   # tracked-mode options (see tracked_render.DEFAULT_OPTIONS)

    @model_validator(mode="after")
    def _check_trim_range(self):
        if self.start_sec is not None and self.end_sec is not None and self.end_sec <= self.start_sec:
            raise ValueError("end_sec must be strictly greater than start_sec")
        return self


class BatchShipRequest(BaseModel):
    project_id: str
    layout_mode: str = Field(default="FACECAM + GAME + CAPS", max_length=40)
    crop_x: float = Field(default=0.50, ge=0.05, le=0.95)
    font_size: int = Field(default=40, ge=16, le=96)
    caption_style: Literal["pink", "yellow", "cyan", "white", "tiktok_white", "tiktok_yellow"] = "pink"
    playback_speed: float = Field(default=1.0, ge=0.25, le=2.0)
    upscale_sharpen: bool = False
    tracked: dict[str, Any] | None = None


class FullPipelineRequest(BaseModel):
    project_id: str
    strip_silence: bool = False
    model_size: Literal["tiny", "base", "small", "medium"] = "tiny"
    language: str = Field(default="auto", min_length=2, max_length=8)
    layout_mode: str = Field(default="FACECAM + GAME + CAPS", max_length=40)
    tracked: dict[str, Any] | None = None


class UpdateProjectRequest(BaseModel):
    captions: list[dict[str, Any]] | None = Field(default=None, max_length=2000)
    crop_x: float | None = Field(default=None, ge=0.05, le=0.95)
    font_size: int | None = Field(default=None, ge=16, le=96)
    layout_mode: str | None = Field(default=None, max_length=40)
    face_box: dict[str, Any] | None = None
    highlight: dict[str, Any] | None = None
    tracked_options: dict[str, Any] | None = None


class AgentCrewRequest(BaseModel):
    project_id: str
    export_mp4: bool = True


@app.get("/api/agents/live")
def api_agents_live(since: int = 0):
    return get_live_agent_state(since_seq=since)


@app.post("/api/agents/run")
def api_agents_run(req: AgentCrewRequest):
    jid = _create_job("agent_crew")
    _run_bg(jid, run_autonomous_agent_crew, project_id=req.project_id, export_mp4=req.export_mp4)
    return {"job_id": jid}


@app.get("/api/mode")
def api_mode():
    """Active LLM mode (cloud Groq / local Ollama / off) for the UI badge."""
    return llm.llm_status()


@app.get("/api/repos")
def api_repos():
    return {"repos": check_tools_status()}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    return JOBS[job_id]


@app.get("/api/projects")
def api_list_projects():
    items = []
    for p in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        meta = p / "project.json"
        if meta.exists():
            try:
                data = load_project(p.name)
                items.append(
                    {
                        "id": data["id"],
                        "name": data.get("name", "SOURCE_01"),
                        "duration": data.get("info", {}).get("duration", 0),
                        "stage_completed": data.get("stage_completed", []),
                    }
                )
            except Exception:
                pass
    return {"projects": items}


@app.get("/api/projects/{project_id}")
def api_get_project(project_id: str):
    try:
        return load_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")


@app.patch("/api/projects/{project_id}")
def api_update_project(project_id: str, req: UpdateProjectRequest):
    try:
        validate_project_id(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    with _project_lock(project_id):
        try:
            project = load_project(project_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Project not found")
        if req.captions is not None:
            project["captions"] = req.captions
        if req.crop_x is not None:
            project["crop_x"] = req.crop_x
        if req.font_size is not None:
            project["font_size"] = req.font_size
        if req.layout_mode is not None:
            project["layout_mode"] = req.layout_mode
        if req.face_box is not None:
            project["face_box"] = req.face_box
        if req.highlight is not None:
            project["highlight"] = req.highlight
        if req.tracked_options is not None:
            project.setdefault("tracked", {})["options"] = tracked_render.normalize_options(req.tracked_options)
        save_project(project_id, project)
        return project


@app.post("/api/pull")
def api_pull(req: PullRequest):
    jid = _create_job("pull")
    _run_bg(
        jid,
        stage_01_pull,
        source_type=req.source_type,
        url=req.url,
        title=req.title,
        clip_start=req.clip_start,
        clip_end=req.clip_end,
        record_seconds=req.record_seconds,
    )
    return {"job_id": jid}


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), title: str = Form(default="SOURCE_01")):
    raw_name = Path(file.filename or "upload.mp4").name
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name) or "upload.mp4"
    tmp_path = (PROJECTS_DIR / f"upload_{uuid.uuid4().hex[:8]}_{safe_name}").resolve()
    if not tmp_path.is_relative_to(PROJECTS_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid upload filename")
    written = 0
    try:
        with tmp_path.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"Fichier trop volumineux (limite {MAX_UPLOAD_MB} Mo)")
                f.write(chunk)
        jid = _create_job("upload")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    def _pull_and_cleanup(**kwargs):
        try:
            return stage_01_pull(source_type="local", local_file=tmp_path, title=title, **kwargs)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    _run_bg(jid, _pull_and_cleanup)
    return {"job_id": jid}


@app.post("/api/stage/find")
def api_stage_find(req: StageFindRequest):
    jid = _create_job("find")
    _run_bg(jid, stage_02_find, project_id=req.project_id, strip_silence=req.strip_silence)
    return {"job_id": jid}


@app.post("/api/stage/track")
def api_stage_track(req: StageTrackRequest):
    jid = _create_job("track")
    _run_bg(jid, stage_03_track, project_id=req.project_id)
    return {"job_id": jid}


@app.post("/api/stage/caption")
def api_stage_caption(req: StageCaptionRequest):
    jid = _create_job("caption")
    _run_bg(jid, stage_04_caption, project_id=req.project_id, model_size=req.model_size, language=req.language)
    return {"job_id": jid}


@app.post("/api/stage/ship")
def api_stage_ship(req: StageShipRequest):
    jid = _create_job("ship")
    _run_bg(
        jid,
        stage_05_ship,
        project_id=req.project_id,
        layout_mode=req.layout_mode,
        crop_x=req.crop_x,
        font_size=req.font_size,
        caption_style=req.caption_style,
        use_demucs=req.use_demucs,
        lossless_trim=req.lossless_trim,
        playback_speed=req.playback_speed,
        upscale_sharpen=req.upscale_sharpen,
        retention_bar=req.retention_bar,
        normalize_audio=req.normalize_audio,
        start_sec=req.start_sec,
        end_sec=req.end_sec,
        tracked=req.tracked,
    )
    return {"job_id": jid}


@app.post("/api/stage/batch-ship")
def api_stage_batch_ship(req: BatchShipRequest):
    jid = _create_job("batch_ship")
    _run_bg(
        jid,
        stage_05_batch_ship,
        project_id=req.project_id,
        layout_mode=req.layout_mode,
        crop_x=req.crop_x,
        font_size=req.font_size,
        caption_style=req.caption_style,
        playback_speed=req.playback_speed,
        upscale_sharpen=req.upscale_sharpen,
        tracked=req.tracked,
    )
    return {"job_id": jid}


@app.post("/api/stage/remotion")
def api_stage_remotion(req: StageTrackRequest):
    jid = _create_job("remotion")
    _run_bg(jid, stage_remotion_render, project_id=req.project_id)
    return {"job_id": jid}


@app.delete("/api/outputs/{filename}")
def api_delete_output(filename: str, project_id: str | None = None):
    fpath = (OUTPUTS_DIR / Path(filename).name).resolve()
    if not fpath.is_relative_to(OUTPUTS_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if fpath.exists():
        fpath.unlink(missing_ok=True)
    if project_id:
        try:
            validate_project_id(project_id)
            with _project_lock(project_id):
                proj = load_project(project_id)
                proj["exports"] = [e for e in proj.get("exports", []) if e.get("filename") != filename]
                save_project(project_id, proj)
        except Exception:
            pass
    return {"ok": True, "deleted": filename}


@app.get("/api/projects/{project_id}/viral-kit")
def api_viral_kit(project_id: str):
    try:
        proj = load_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")
    return generate_viral_kit(proj)


@app.post("/api/open-outputs")
def api_open_outputs(request: Request):
    """Open the outputs folder in the OS file manager (only for a browser running on this machine)."""
    import subprocess
    import sys

    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="Only available from the local machine")
    path = str(OUTPUTS_DIR)
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "path": path}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": path}


@app.delete("/api/projects/{project_id}")
def api_delete_project(project_id: str):
    try:
        pdir = validate_project_id(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not pdir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    with _project_lock(project_id):
        shutil.rmtree(pdir, ignore_errors=True)
    return {"ok": True, "deleted": project_id}


@app.get("/api/remotion-json")
def api_remotion_json():
    fpath = BASE_DIR / "remotion" / "src" / "captions.json"
    if not fpath.exists():
        return JSONResponse({"project_id": None, "fps": 30, "captions": []})
    return FileResponse(fpath, media_type="application/json", filename="remotion_captions.json")


@app.get("/api/health")
def api_health():
    repos = check_tools_status()
    outputs = list(OUTPUTS_DIR.glob("*.mp4"))
    projects = [p for p in PROJECTS_DIR.iterdir() if p.is_dir() and (p / "project.json").exists()]
    return {
        "status": "ok",
        # Honest breakdown: only "executed" tools run their own code in the pipeline.
        "tools_ready": sum(1 for r in repos if r["ready"]),
        "tools_total": len(repos),
        "tools_executed": sum(1 for r in repos if r["status"] == "executed"),
        "tools_executed_missing": sum(1 for r in repos if r["status"] == "executed_missing"),
        "tools_equivalent": sum(1 for r in repos if r["status"] == "equivalent"),
        "tools_note": "tools_ready = outils tiers exécutés directement. Les moteurs équivalents (réimplémentés) ne sont pas comptés.",
        "projects_count": len(projects),
        "exports_count": len(outputs),
    }


@app.get("/api/projects/{project_id}/srt")
def api_download_srt(project_id: str):
    try:
        pdir = validate_project_id(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    srt_path = (pdir / "captions.srt").resolve()
    if not srt_path.is_relative_to(pdir) or not srt_path.exists():
        raise HTTPException(status_code=404, detail="SRT not generated yet. Run 04 / CAPTION first.")
    return FileResponse(srt_path, media_type="text/plain", filename=f"{project_id}_captions.srt")


@app.post("/api/stage/full")
def api_stage_full(req: FullPipelineRequest):
    jid = _create_job("full")

    def _run_all(progress=None, project_id=None):
        pid = project_id or req.project_id
        stage_02_find(pid, strip_silence=req.strip_silence, progress=progress)
        stage_03_track(pid, progress=progress)
        stage_04_caption(pid, model_size=req.model_size, language=req.language, progress=progress)
        proj = load_project(pid)
        hl = proj.get("highlight", {})
        return stage_05_ship(
            pid,
            layout_mode=req.layout_mode,
            crop_x=proj.get("crop_x", 0.5),
            font_size=proj.get("font_size", 40),
            start_sec=hl.get("start"),
            end_sec=hl.get("end"),
            tracked=req.tracked,
            progress=progress,
        )

    _run_bg(jid, _run_all, project_id=req.project_id)
    return {"job_id": jid}


@app.get("/media/projects/{project_id}/{subpath:path}")
def serve_project_media(project_id: str, subpath: str):
    try:
        pdir = validate_project_id(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project_id")
    fpath = (pdir / subpath).resolve()
    if not fpath.is_relative_to(pdir) or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(fpath)


@app.get("/media/outputs/{filename}")
def serve_output_media(filename: str):
    fpath = (OUTPUTS_DIR / Path(filename).name).resolve()
    if not fpath.is_relative_to(OUTPUTS_DIR.resolve()) or not fpath.exists():
        raise HTTPException(status_code=404, detail="Export not found")
    return FileResponse(fpath, media_type="video/mp4", filename=Path(filename).name)


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)
