"""Automatic and manual cleanup of old project workdirs, intermediates and exports.

Never deletes the queue file, the jobs file, or a project that is currently running.
TTL is ``CLIP_MASTER_CACHE_TTL_HOURS`` (default 48; 0 disables automatic purges).
A free-space guard (``CLIP_MASTER_MIN_FREE_MB``, default 1024, 0 disables) can refuse new jobs.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

PROTECTED_FILE_NAMES = frozenset({
    "_queue.json",
    "_queue.json.tmp",
    "_jobs.json",
    "_jobs.json.tmp",
})
PROTECTED_DIR_NAMES = frozenset({"_queue_files"})
INTERMEDIATE_SUFFIXES = (".tmp", ".part", ".ytdl")
# When a job is running in another process, dirs touched this recently stay protected.
RUNNING_GRACE_SECONDS = 15 * 60

_extra_protected: set[str] = set()
_extra_lock = threading.Lock()
_last_purge: dict[str, Any] | None = None
_sweeper_thread: threading.Thread | None = None
_sweeper_stop = threading.Event()


def _as_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def protect(identity: str | Path) -> None:
    with _extra_lock:
        _extra_protected.add(str(identity))


def unprotect(identity: str | Path) -> None:
    with _extra_lock:
        _extra_protected.discard(str(identity))


@contextmanager
def protecting(identity: str | Path) -> Iterator[None]:
    protect(identity)
    try:
        yield
    finally:
        unprotect(identity)


def extra_protected() -> set[str]:
    with _extra_lock:
        return set(_extra_protected)


def human_bytes(n: int) -> str:
    n = max(0, int(n))
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            val = n / size
            return f"{val:.1f} {unit}"
    return f"{n} B"


def path_size(path: Path) -> int:
    try:
        if path.is_file() or path.is_symlink():
            return path.stat().st_size
        total = 0
        for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
            for name in files:
                fp = Path(root) / name
                try:
                    total += fp.stat().st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def path_mtime(path: Path) -> float:
    try:
        mtimes = [path.stat().st_mtime]
        if path.is_dir():
            meta = path / "project.json"
            if meta.exists():
                mtimes.append(meta.stat().st_mtime)
        return max(mtimes)
    except OSError:
        return 0.0


def ttl_hours_from_env() -> int:
    try:
        return int((os.getenv("CLIP_MASTER_CACHE_TTL_HOURS") or "48").strip())
    except ValueError:
        return 48


def min_free_mb_from_env() -> int:
    try:
        return int((os.getenv("CLIP_MASTER_MIN_FREE_MB") or "1024").strip())
    except ValueError:
        return 1024


def sweep_seconds_from_env() -> int:
    try:
        return int((os.getenv("CLIP_MASTER_CACHE_SWEEP_SECONDS") or "1800").strip())
    except ValueError:
        return 1800


def disk_status(path: Path, min_free_mb: int | None = None) -> dict[str, Any]:
    if min_free_mb is None:
        min_free_mb = min_free_mb_from_env()
    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    free_mb = usage.free // (1024 * 1024)
    return {
        "path": str(path.resolve()),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_mb": int(free_mb),
        "min_free_mb": int(min_free_mb),
        "ok": min_free_mb <= 0 or usage.free >= min_free_mb * 1024 * 1024,
    }


def disk_status_for_app() -> dict[str, Any]:
    from config import PROJECTS_DIR

    return disk_status(PROJECTS_DIR)


def last_purge() -> dict[str, Any] | None:
    return dict(_last_purge) if _last_purge else None


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def protected_from_stores(
    projects_dir: Path,
    *,
    now: float | None = None,
    grace_seconds: int = RUNNING_GRACE_SECONDS,
) -> set[Path]:
    """Paths that must never be deleted, including work from a running server process."""
    now = time.time() if now is None else now
    projects_dir = projects_dir.resolve()
    protected: set[Path] = set()
    for name in PROTECTED_FILE_NAMES:
        protected.add(projects_dir / name)
    queue_files = projects_dir / "_queue_files"
    protected.add(queue_files)

    running = False
    queue = _load_json(projects_dir / "_queue.json") or {}
    for it in queue.get("items") or []:
        if not isinstance(it, dict):
            continue
        if it.get("status") in ("running", "queued"):
            running = running or it.get("status") == "running"
            pid = it.get("project_id")
            if pid:
                protected.add(projects_dir / str(pid))
        if it.get("kind") == "file" and it.get("source"):
            try:
                protected.add(_as_path(it["source"]))
            except Exception:
                pass

    jobs = _load_json(projects_dir / "_jobs.json") or {}
    if isinstance(jobs, dict):
        for j in jobs.values():
            if not isinstance(j, dict):
                continue
            if j.get("status") == "running":
                running = True
                pid = j.get("project_id")
                if pid:
                    protected.add(projects_dir / str(pid))

    if running and grace_seconds > 0:
        try:
            for p in projects_dir.iterdir():
                if p.name in PROTECTED_FILE_NAMES or p.name in PROTECTED_DIR_NAMES:
                    continue
                if p.is_dir() and now - path_mtime(p) <= grace_seconds:
                    protected.add(p.resolve())
        except OSError:
            pass

    for ident in extra_protected():
        candidate = Path(ident)
        protected.add(candidate if candidate.is_absolute() else projects_dir / ident)

    return {p.resolve() if p.exists() or p.is_absolute() else (projects_dir / p.name).resolve() for p in protected}


def _is_protected(path: Path, protected: set[Path], projects_dir: Path) -> bool:
    resolved = path.resolve()
    if path.name in PROTECTED_FILE_NAMES or path.name in PROTECTED_DIR_NAMES:
        return True
    if resolved in protected:
        return True
    for p in protected:
        # The queue-files directory itself is protected, but unreferenced
        # orphans inside it may still be deleted.
        if p.name in PROTECTED_DIR_NAMES:
            continue
        try:
            if resolved == p or resolved.is_relative_to(p):
                return True
        except (OSError, ValueError):
            continue
    return False


def _expired(path: Path, *, now: float, ttl_hours: int, all_unused: bool) -> bool:
    if all_unused:
        return True
    if ttl_hours <= 0:
        return False
    return (now - path_mtime(path)) >= ttl_hours * 3600


def _delete(path: Path, dry_run: bool) -> int:
    size = path_size(path)
    if dry_run:
        return size
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return 0
    return size if not path.exists() else 0


def _record(items: list[dict[str, Any]], path: Path, reason: str, size: int = 0) -> None:
    items.append({"path": str(path), "reason": reason, "bytes": size})


def purge(
    *,
    projects_dir: Path,
    outputs_dir: Path,
    ttl_hours: int | None = None,
    all_unused: bool = False,
    dry_run: bool = False,
    now: float | None = None,
    protect: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    """Delete expired (or, with all_unused, all unused) workdirs, intermediates and exports."""
    projects_dir = Path(projects_dir)
    outputs_dir = Path(outputs_dir)
    projects_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    if ttl_hours is None:
        ttl_hours = ttl_hours_from_env()
    now = time.time() if now is None else now

    protected = protected_from_stores(projects_dir, now=now)
    if protect:
        for item in protect:
            protected.add(_as_path(item) if Path(item).is_absolute() else (projects_dir / str(item)).resolve())

    deleted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    freed = 0

    # --- project workdirs and loose files in projects/ ---------------------
    try:
        entries = list(projects_dir.iterdir())
    except OSError:
        entries = []
    for path in entries:
        if _is_protected(path, protected, projects_dir):
            _record(skipped, path, "protected")
            continue
        if path.is_dir():
            if _expired(path, now=now, ttl_hours=ttl_hours, all_unused=all_unused):
                size = _delete(path, dry_run)
                freed += size
                _record(deleted, path, "project", size)
            elif ttl_hours > 0 or all_unused:
                # Drop stale intermediate files inside a project we keep.
                for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
                    for name in files:
                        fp = Path(root) / name
                        if _is_protected(fp, protected, projects_dir):
                            continue
                        if fp.suffix.lower() in INTERMEDIATE_SUFFIXES and _expired(
                            fp, now=now, ttl_hours=ttl_hours, all_unused=all_unused
                        ):
                            size = _delete(fp, dry_run)
                            freed += size
                            _record(deleted, fp, "intermediate", size)
        elif path.is_file() and path.suffix.lower() in INTERMEDIATE_SUFFIXES:
            if _expired(path, now=now, ttl_hours=ttl_hours, all_unused=all_unused):
                size = _delete(path, dry_run)
                freed += size
                _record(deleted, path, "intermediate", size)

    # Orphan upload files in _queue_files that are not referenced
    queue_files = projects_dir / "_queue_files"
    if queue_files.is_dir():
        for fp in queue_files.iterdir():
            if _is_protected(fp, protected, projects_dir):
                _record(skipped, fp, "protected")
                continue
            if _expired(fp, now=now, ttl_hours=ttl_hours, all_unused=all_unused):
                size = _delete(fp, dry_run)
                freed += size
                _record(deleted, fp, "queue_orphan", size)

    # --- exports -----------------------------------------------------------
    try:
        out_entries = list(outputs_dir.iterdir())
    except OSError:
        out_entries = []
    for path in out_entries:
        if _is_protected(path, protected, projects_dir):
            _record(skipped, path, "protected")
            continue
        if _expired(path, now=now, ttl_hours=ttl_hours, all_unused=all_unused):
            size = _delete(path, dry_run)
            freed += size
            _record(deleted, path, "export", size)

    report = {
        "ok": True,
        "freed_bytes": int(freed),
        "freed_human": human_bytes(freed),
        "deleted": deleted,
        "skipped": [{"path": s["path"], "reason": s["reason"]} for s in skipped],
        "ttl_hours": int(ttl_hours),
        "all": bool(all_unused),
        "dry_run": bool(dry_run),
        "at": now,
    }
    global _last_purge
    if not dry_run:
        _last_purge = report
    return report


def purge_now(
    *,
    all_unused: bool = False,
    ttl_hours: int | None = None,
    dry_run: bool = False,
    protect: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    from config import OUTPUTS_DIR, PROJECTS_DIR

    return purge(
        projects_dir=PROJECTS_DIR,
        outputs_dir=OUTPUTS_DIR,
        ttl_hours=ttl_hours,
        all_unused=all_unused,
        dry_run=dry_run,
        protect=protect,
    )


def auto_purge() -> dict[str, Any] | None:
    """Startup / periodic sweep. No-op when TTL is 0."""
    if ttl_hours_from_env() <= 0:
        return None
    return purge_now(all_unused=False)


def start_sweeper(interval: int | None = None) -> threading.Event:
    """Start a daemon thread. Interval 0 (or negative) disables the periodic sweep."""
    global _sweeper_thread
    if interval is None:
        interval = sweep_seconds_from_env()
    _sweeper_stop.clear()
    if interval <= 0:
        return _sweeper_stop

    def _loop() -> None:
        while not _sweeper_stop.wait(interval):
            try:
                auto_purge()
            except Exception:
                pass

    if _sweeper_thread and _sweeper_thread.is_alive():
        return _sweeper_stop
    _sweeper_thread = threading.Thread(target=_loop, name="cache-gc", daemon=True)
    _sweeper_thread.start()
    return _sweeper_stop


def stop_sweeper() -> None:
    _sweeper_stop.set()
