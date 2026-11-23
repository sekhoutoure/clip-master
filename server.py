from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import json
import os
import re
import shutil
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
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
    held_run_locks,
    load_project,
    save_project,
    stage_01_pull,
    stage_02_find,
    stage_03_track,
    stage_04_caption,
    stage_05_batch_ship,
    stage_05_ship,
    stage_remotion_render,
    stage_translate,
    validate_project_id,
)
import tracked_render
from config import BASE_DIR, EXTRA_ORIGINS, HOST, MAX_UPLOAD_MB, PORT, TOKEN
import cache_gc
import device
import llm
import messages
import presets
import security
from batch_queue import BatchQueue
from tools_registry import check_tools_status

logging.basicConfig(
    level=(os.getenv("CLIP_MASTER_LOG_LEVEL") or "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("clip_master.server")

# Fail closed: a non-loopback bind needs a token (checked again by `clip-master serve`).
security.check_startup(HOST, TOKEN)
NETWORK_MODE = not security.is_loopback_host(HOST)
AUTH_REQUIRED = bool(TOKEN)  # network mode implies a token (check_startup); optional on loopback
if NETWORK_MODE:
    logger.warning("Network mode: listening on %s with token authentication.", HOST)

STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        cache_gc.auto_purge()
    except Exception:
        logger.warning("Startup cache purge failed", exc_info=True)
    cache_gc.start_sweeper()
    QUEUE.start()
    yield
    cache_gc.stop_sweeper()
    QUEUE.stop()


app = FastAPI(title="CLIP MASTER — 9:16 clip studio", lifespan=_lifespan)

ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}", f"http://{HOST}:{PORT}", *EXTRA_ORIGINS}
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024  # hard quota, set CLIP_MASTER_MAX_UPLOAD_MB in .env
MAX_CONCURRENT_JOBS = 1

# Host header allow-list (blocks DNS-rebinding: a hostile site resolving its own name to 127.0.0.1)
_hosts = {"localhost", "127.0.0.1", "[::1]", "::1", HOST} | {h.strip() for h in os.getenv("CLIP_MASTER_ALLOWED_HOSTS", "").split(",") if h.strip()}
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"] if HOST in ("0.0.0.0", "::") else sorted(_hosts))

# The UI is same-origin and uses no cross-origin credentials, so CORS stays minimal: only the known origins,
# explicit methods and headers, no cookies. (Authentication = token header or same-origin cookie, see auth_guard.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Clip-Master-Token", "X-Lang"],
)


def _origin_allowed(request: Request, origin: str) -> bool:
    if origin in ALLOWED_ORIGINS:
        return True
    # Network mode: the UI is opened through the server's own address (LAN IP, host name), which is
    # same-origin as the request's Host header. Token auth (SameSite=Strict cookie) protects it.
    return NETWORK_MODE and origin in (f"http://{request.headers.get('host', '')}", f"https://{request.headers.get('host', '')}")


@app.middleware("http")
async def csrf_origin_guard(request: Request, call_next):
    """Reject cross-site and headerless mutating requests (strict anti-CSRF Origin/Referer check).

    Scripts that authenticate with the token header are exempt: a browser never attaches that header on its own.
    """
    if (
        request.method in ("POST", "PUT", "PATCH", "DELETE")
        and not getattr(request.state, "header_authenticated", False)
        and request.url.path != "/login"  # sign-in form: it only ever grants a session to someone who knows the token
    ):
        origin = (request.headers.get("origin") or "").rstrip("/")
        referer = request.headers.get("referer") or ""
        if origin:
            if not _origin_allowed(request, origin):
                return JSONResponse({"detail": messages.t("csrf.cross_site")}, status_code=403)
        elif referer:
            ref_origin = "/".join(referer.split("/", 3)[:3])  # scheme://host[:port]
            if not any(referer.startswith(o + "/") or referer == o for o in ALLOWED_ORIGINS) and not _origin_allowed(request, ref_origin):
                return JSONResponse({"detail": messages.t("csrf.cross_site_referer")}, status_code=403)
        else:
            return JSONResponse({"detail": messages.t("csrf.missing")}, status_code=403)
    return await call_next(request)


_OPEN_PATHS = {"/api/health"}  # liveness probe used by start.sh / start.bat: reveals no project data


def _session_redirect(request: Request) -> RedirectResponse:
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        security.COOKIE_NAME, security.cookie_value(TOKEN), httponly=True, samesite="strict",
        secure=request.url.scheme == "https", max_age=30 * 24 * 3600, path="/",
    )
    return resp


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    """Language per request + token check. Active when CLIP_MASTER_TOKEN is set (mandatory off-loopback).

    Accepted: ``Authorization: Bearer <token>`` or ``X-Clip-Master-Token`` (scripts), or the cookie the UI gets
    after opening ``/?token=<token>`` once.
    """
    messages.set_lang(messages.lang_from_headers(request.headers))
    if not AUTH_REQUIRED or request.method == "OPTIONS" or request.url.path in _OPEN_PATHS or (
        request.url.path == "/login" and request.method == "POST"
    ):
        return await call_next(request)
    if security.header_token_ok(request.headers, TOKEN):
        request.state.header_authenticated = True
        return await call_next(request)
    if security.cookie_ok(request.cookies, TOKEN):
        return await call_next(request)
    if request.url.path == "/" and request.method == "GET":
        given = request.query_params.get("token")
        if security.query_token_ok(given, TOKEN):
            return _session_redirect(request)
        return HTMLResponse(security.login_page(error=given is not None), status_code=401 if given is not None else 200)
    logger.warning("Unauthenticated request refused: %s %s", request.method, request.url.path)
    return JSONResponse({"detail": messages.t("auth.required")}, status_code=401, headers={"WWW-Authenticate": "Bearer"})

JOBS_FILE = PROJECTS_DIR / "_jobs.json"
_JOBS_LOCK = threading.RLock()


def _load_jobs() -> dict[str, dict[str, Any]]:
    if JOBS_FILE.exists():
        try:
            data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
            for j in data.values():
                if j.get("status") == "running":
                    j["status"] = "error"
                    j["error"] = messages.t("server.restarted", lang=j.get("lang"))
            return data
        except Exception:
            logger.warning("Could not read %s, starting with no job history", JOBS_FILE, exc_info=True)
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
            logger.warning("Could not persist the jobs file", exc_info=True)


JOBS: dict[str, dict[str, Any]] = _load_jobs()


def _ensure_disk() -> dict:
    """Refuse new work when the data volume is below the configured free-space floor."""
    st = cache_gc.disk_status_for_app()
    if not st["ok"]:
        raise HTTPException(
            status_code=507,
            detail=messages.t("server.disk_low", free_mb=st["free_mb"], min_free_mb=st["min_free_mb"]),
        )
    return st


def _runtime_protect() -> list[str]:
    ids = set(held_run_locks())
    with _JOBS_LOCK:
        for j in JOBS.values():
            if j.get("status") == "running" and j.get("project_id"):
                ids.add(str(j["project_id"]))
    for it in QUEUE.list():
        if it.get("status") in ("running", "queued") and it.get("project_id"):
            ids.add(str(it["project_id"]))
    return list(ids)


def _create_job(kind: str) -> str:
    _ensure_disk()
    with _JOBS_LOCK:
        running = sum(1 for j in JOBS.values() if j.get("status") == "running") + (1 if QUEUE.is_running() else 0)
        if running >= MAX_CONCURRENT_JOBS:
            raise HTTPException(
                status_code=429,
                detail=messages.t("server.too_many_jobs", running=running, max=MAX_CONCURRENT_JOBS),
            )
        jid = uuid.uuid4().hex[:10]
        JOBS[jid] = {
            "id": jid,
            "kind": kind,
            "lang": messages.get_lang(),
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
    "01": ("01 Téléchargement", "PULL", "pink", "#ee1d6c", ".ingest-bar"),
    "02": ("02 Enregistrement live", "PULL", "indigo", "#6366f1", "#monitorWrap"),
    "03": ("03 VOD + chat", "PULL", "teal", "#0e9f8e", ".bin-card"),
    "04": ("04 Coupes de scène", "FIND", "orange", "#f59e0b", "#filmstripTrack"),
    "05": ("05 Coupe des silences", "FIND", "indigo", "#6366f1", "#audioTrack"),
    "06": ("06 Directeur de moments", "FIND", "pink", "#ee1d6c", "#momentsGrid"),
    "07": ("07 Choix de la fenêtre", "FIND", "pink", "#ee1d6c", "#highlightBox"),
    "08": ("08 Facecam", "TRACK", "pink", "#ee1d6c", "#faceBox"),
    "09": ("09 Suivi du sujet", "TRACK", "teal", "#0e9f8e", "#cropBox"),
    "11": ("11 Rédaction des sous-titres", "CAPTION", "orange", "#f59e0b", "#captionInput"),
    "12": ("12 Transcription", "CAPTION", "pink", "#ee1d6c", "#captionsTrack"),
    "14": ("14 Séparation de la voix", "SHIP", "orange", "#f59e0b", "#audioTrack"),
    "18": ("18 Sous-titres animés", "CAPTION", "indigo", "#6366f1", ".inspector-card"),
    "20": ("20 Export", "SHIP", "pink", "#ee1d6c", "#btnExport"),
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


def _remember_job_project(jid: str, pid: str | None) -> None:
    if not pid:
        return
    with _JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid]["project_id"] = pid


def _run_bg(jid: str, fn, *args, **kwargs) -> None:
    target_pid = kwargs.get("project_id")
    job_lang = messages.get_lang()

    def worker():
        messages.set_lang(job_lang)
        if target_pid:
            _remember_job_project(jid, target_pid)
            cache_gc.protect(target_pid)
        try:
            if target_pid:
                validate_project_id(target_pid)
                with project_run_lock(target_pid):
                    res = fn(*args, progress=lambda s, p, m, r: _update_job(jid, s, p, m, r), **kwargs)
            else:
                res = fn(*args, progress=lambda s, p, m, r: _update_job(jid, s, p, m, r), **kwargs)
            pid = None
            if isinstance(res, dict):
                pid = res.get("project_id") or res.get("id")
                if isinstance(res.get("project"), dict):
                    pid = pid or res["project"].get("id")
            _remember_job_project(jid, pid)
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
            if target_pid:
                cache_gc.unprotect(target_pid)
            threading.Timer(4.0, clear_active_agents).start()

    t = threading.Thread(target=worker, daemon=True)
    t.start()



# ---------------------------------------------------------------------------
# Batch queue (several VODs / files, persisted across restarts)
# ---------------------------------------------------------------------------
QUEUE_FILES = PROJECTS_DIR / "_queue_files"


def _queue_runner(item: dict[str, Any], progress) -> dict[str, Any]:
    """Full pipeline for one queue item: pull -> find -> track -> caption -> (translate) -> ship."""
    o = item.get("options") or {}
    if item["kind"] == "url":
        proj = stage_01_pull(o.get("source_type", "ytdlp"), url=item["source"], title=item.get("label"),
                             clip_start=o.get("clip_start"), clip_end=o.get("clip_end"), progress=progress)
    else:
        proj = stage_01_pull("local", local_file=Path(item["source"]), title=item.get("label"), progress=progress)
    pid = proj["id"]
    with project_run_lock(pid):
        stage_02_find(pid, strip_silence=bool(o.get("strip_silence")), progress=progress)
        stage_03_track(pid, progress=progress)
        stage_04_caption(pid, model_size=o.get("model_size", "tiny"), language=o.get("language", "auto"), progress=progress)
        if o.get("translate_to"):
            stage_translate(pid, o["translate_to"], progress=progress)
        p = load_project(pid)
        hl = p.get("highlight", {})
        res = stage_05_ship(
            pid, layout_mode=o.get("layout_mode", "FACECAM + GAME + CAPS"), crop_x=p.get("crop_x", 0.5),
            font_size=p.get("font_size", 40), start_sec=hl.get("start"), end_sec=hl.get("end"),
            tracked=o.get("tracked"), export_preset=o.get("export_preset"), progress=progress,
        )
    return {"project_id": pid, "output": res["export"]["filename"]}


QUEUE = BatchQueue(
    PROJECTS_DIR / "_queue.json", QUEUE_FILES, _queue_runner,
    busy=lambda: any(j.get("status") == "running" for j in list(JOBS.values())),
)


class QueueOptions(BaseModel):
    export_preset: Literal["tiktok", "youtube_shorts", "instagram_reels"] | None = None
    translate_to: Literal["fr", "en", "es", "de", "pt", "it", "ar"] | None = None
    source_type: Literal["ytdlp", "twitchdownloader"] = "ytdlp"
    model_size: Literal["tiny", "base", "small", "medium"] = "tiny"
    language: str = Field(default="auto", min_length=2, max_length=8)
    layout_mode: str = Field(default="FACECAM + GAME + CAPS", max_length=40)
    strip_silence: bool = False
    tracked: dict[str, Any] | None = None


class QueueAddRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=50)
    options: QueueOptions = QueueOptions()


@app.get("/api/queue")
def api_queue():
    return {"items": QUEUE.list(), "running": QUEUE.is_running()}


@app.post("/api/queue")
def api_queue_add(req: QueueAddRequest):
    from pipeline import validate_source_url

    _ensure_disk()
    try:
        urls = [validate_source_url(u) for u in req.urls]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        added = [QUEUE.add("url", u, label=u.rsplit("/", 1)[-1].split("?")[0] or u, options=req.options.model_dump()) for u in urls]
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"added": added}


@app.post("/api/queue/upload")
async def api_queue_upload(file: UploadFile = File(...), options: str = Form(default="{}")):
    _ensure_disk()
    try:
        opts = QueueOptions(**json.loads(options or "{}")).model_dump()
    except Exception:
        raise HTTPException(status_code=400, detail=messages.t("server.bad_options"))
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file.filename or "upload.mp4").name) or "upload.mp4"
    QUEUE_FILES.mkdir(parents=True, exist_ok=True)
    dest = QUEUE_FILES / f"{uuid.uuid4().hex[:8]}_{safe}"
    written = 0
    try:
        with dest.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=messages.t("server.too_large", limit_mb=MAX_UPLOAD_MB))
                f.write(chunk)
        item = QUEUE.add("file", str(dest), label=safe, options=opts)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return {"added": [item]}


def _queue_call(fn, item_id: str):
    try:
        return fn(item_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=messages.t("server.item_not_found"))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/queue/{item_id}/cancel")
def api_queue_cancel(item_id: str):
    return _queue_call(QUEUE.cancel, item_id)


@app.post("/api/queue/{item_id}/retry")
def api_queue_retry(item_id: str):
    return _queue_call(QUEUE.retry, item_id)


@app.delete("/api/queue/{item_id}")
def api_queue_remove(item_id: str):
    _queue_call(QUEUE.remove, item_id)
    return {"ok": True}


@app.post("/api/queue-clear")
def api_queue_clear():
    return {"removed": QUEUE.clear_finished()}


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
    export_preset: Literal["tiktok", "youtube_shorts", "instagram_reels"] | None = None

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
    export_preset: Literal["tiktok", "youtube_shorts", "instagram_reels"] | None = None


class FullPipelineRequest(BaseModel):
    project_id: str
    strip_silence: bool = False
    model_size: Literal["tiny", "base", "small", "medium"] = "tiny"
    language: str = Field(default="auto", min_length=2, max_length=8)
    layout_mode: str = Field(default="FACECAM + GAME + CAPS", max_length=40)
    tracked: dict[str, Any] | None = None
    export_preset: Literal["tiktok", "youtube_shorts", "instagram_reels"] | None = None
    translate_to: Literal["fr", "en", "es", "de", "pt", "it", "ar"] | None = None


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
    export_preset: Literal["tiktok", "youtube_shorts", "instagram_reels"] | None = None


@app.get("/api/agents/live")
def api_agents_live(since: int = 0):
    return get_live_agent_state(since_seq=since)


@app.post("/api/agents/run")
def api_agents_run(req: AgentCrewRequest):
    jid = _create_job("agent_crew")
    _run_bg(jid, run_autonomous_agent_crew, project_id=req.project_id, export_mp4=req.export_mp4, export_preset=req.export_preset)
    return {"job_id": jid}


@app.get("/api/presets")
def api_presets():
    return {"presets": presets.list_presets(), "caption_styles": list(presets.ALL_CAPTION_STYLES)}


@app.get("/api/mode")
def api_mode():
    """Active LLM mode (cloud / local / off), compute device and disk for the UI badge."""
    status = llm.llm_status()
    status["device"] = device.device_status()
    status["disk"] = cache_gc.disk_status_for_app()
    return status


@app.get("/api/cache")
def api_cache_status():
    return {
        "disk": cache_gc.disk_status_for_app(),
        "ttl_hours": cache_gc.ttl_hours_from_env(),
        "min_free_mb": cache_gc.min_free_mb_from_env(),
        "last_purge": cache_gc.last_purge(),
    }


@app.post("/api/cache/clean")
def api_cache_clean(all: bool = False):
    """Purge old (or, with all=true, all unused) workdirs, intermediates and exports.

    Never deletes the queue file or a currently running job.
    """
    report = cache_gc.purge_now(all_unused=all, protect=_runtime_protect())
    report["disk"] = cache_gc.disk_status_for_app()
    return report


@app.get("/api/repos")
def api_repos():
    return {"repos": check_tools_status()}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail=messages.t("server.job_not_found"))
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
                logger.warning("Skipping unreadable project %s", p.name, exc_info=True)
    return {"projects": items}


@app.get("/api/projects/{project_id}")
def api_get_project(project_id: str):
    try:
        return load_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=messages.t("server.project_not_found"))


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
            raise HTTPException(status_code=404, detail=messages.t("server.project_not_found"))
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
        raise HTTPException(status_code=400, detail=messages.t("server.invalid_upload_filename"))
    written = 0
    try:
        with tmp_path.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=messages.t("server.too_large", limit_mb=MAX_UPLOAD_MB))
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


class TranslateRequest(BaseModel):
    project_id: str
    target_lang: Literal["fr", "en", "es", "de", "pt", "it", "ar"]


@app.post("/api/stage/translate")
def api_stage_translate(req: TranslateRequest):
    jid = _create_job("translate")
    _run_bg(jid, stage_translate, project_id=req.project_id, target_lang=req.target_lang)
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
        export_preset=req.export_preset,
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
        export_preset=req.export_preset,
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
        raise HTTPException(status_code=400, detail=messages.t("server.invalid_filename"))
    if fpath.exists():
        fpath.unlink(missing_ok=True)
    project_updated: bool | None = None
    if project_id:
        try:
            validate_project_id(project_id)
            with _project_lock(project_id):
                proj = load_project(project_id)
                proj["exports"] = [e for e in proj.get("exports", []) if e.get("filename") != filename]
                save_project(project_id, proj)
            project_updated = True
        except Exception:
            project_updated = False
            logger.warning("Export %s deleted but project %s could not be updated", filename, project_id, exc_info=True)
    return {"ok": True, "deleted": filename, "project_updated": project_updated}


@app.get("/api/projects/{project_id}/viral-kit")
def api_viral_kit(project_id: str):
    try:
        proj = load_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=messages.t("server.project_not_found"))
    return generate_viral_kit(proj)


@app.post("/api/open-outputs")
def api_open_outputs(request: Request):
    """Open the outputs folder in the OS file manager (only for a browser running on this machine)."""
    import subprocess
    import sys

    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail=messages.t("server.local_only"))
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
        raise HTTPException(status_code=404, detail=messages.t("server.project_not_found"))
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
        "tools_note": "tools_ready = fonctions installées et prêtes à s'exécuter.",
        "projects_count": len(projects),
        "exports_count": len(outputs),
        "disk": cache_gc.disk_status_for_app(),
        "device": device.device_status(),
    }


@app.get("/api/projects/{project_id}/srt")
def api_download_srt(project_id: str):
    try:
        pdir = validate_project_id(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    srt_path = (pdir / "captions.srt").resolve()
    if not srt_path.is_relative_to(pdir) or not srt_path.exists():
        raise HTTPException(status_code=404, detail=messages.t("server.srt_missing"))
    return FileResponse(srt_path, media_type="text/plain", filename=f"{project_id}_captions.srt")


@app.post("/api/stage/full")
def api_stage_full(req: FullPipelineRequest):
    jid = _create_job("full")

    def _run_all(progress=None, project_id=None):
        pid = project_id or req.project_id
        stage_02_find(pid, strip_silence=req.strip_silence, progress=progress)
        stage_03_track(pid, progress=progress)
        stage_04_caption(pid, model_size=req.model_size, language=req.language, progress=progress)
        if req.translate_to:
            stage_translate(pid, req.translate_to, progress=progress)
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
            export_preset=req.export_preset,
            progress=progress,
        )

    _run_bg(jid, _run_all, project_id=req.project_id)
    return {"job_id": jid}


@app.get("/media/projects/{project_id}/{subpath:path}")
def serve_project_media(project_id: str, subpath: str):
    try:
        pdir = validate_project_id(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=messages.t("server.invalid_project_id"))
    try:
        fpath = (pdir / subpath).resolve()
        ok = fpath.is_relative_to(pdir) and fpath.is_file()
    except (ValueError, OSError):  # e.g. an embedded NUL byte: not a file, not a 500
        ok = False
    if not ok:
        raise HTTPException(status_code=404, detail=messages.t("server.media_not_found"))
    return FileResponse(fpath)


@app.get("/media/outputs/{filename}")
def serve_output_media(filename: str):
    try:
        fpath = (OUTPUTS_DIR / Path(filename).name).resolve()
        ok = fpath.is_relative_to(OUTPUTS_DIR.resolve()) and fpath.exists()
    except (ValueError, OSError):
        ok = False
    if not ok:
        raise HTTPException(status_code=404, detail=messages.t("server.export_not_found"))
    return FileResponse(fpath, media_type="video/mp4", filename=Path(filename).name)


@app.post("/login")
async def login(request: Request, token: str = Form(default="")):
    """Login form of the web UI: exchanges the token for the session cookie (keeps it out of the URL)."""
    if not AUTH_REQUIRED or security.query_token_ok(token, TOKEN):
        return _session_redirect(request)
    logger.warning("Failed login attempt from %s", request.client.host if request.client else "?")
    await asyncio.sleep(0.5)  # slow down guessing
    return HTMLResponse(security.login_page(error=True), status_code=401)


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/fonts", StaticFiles(directory=str(BASE_DIR / "assets" / "fonts")), name="fonts")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)
