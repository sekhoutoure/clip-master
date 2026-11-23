"""Disk cleanup, TTL purge and low-disk guard. All work happens in tmp dirs."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import cache_gc
import config


def _touch(path: Path, age_hours: float, data: bytes = b"x" * 100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    mtime = time.time() - age_hours * 3600
    os.utime(path, (mtime, mtime))
    return path


def _project(root: Path, name: str, age_hours: float, payload: bytes = b"video" * 50) -> Path:
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    _touch(pdir / "project.json", age_hours, b'{"id":"%s"}' % name.encode())
    _touch(pdir / "source.mp4", age_hours, payload)
    os.utime(pdir, (time.time() - age_hours * 3600, time.time() - age_hours * 3600))
    return pdir


def test_purge_deletes_old_projects_and_exports(tmp_path):
    projects, outputs = tmp_path / "projects", tmp_path / "outputs"
    old_p = _project(projects, "proj_old", 72)
    new_p = _project(projects, "proj_new", 1)
    old_e = _touch(outputs / "old.mp4", 72, b"export-old" * 20)
    new_e = _touch(outputs / "new.mp4", 1, b"export-new" * 20)
    _touch(projects / "stale.part", 72, b"partial")

    report = cache_gc.purge(projects_dir=projects, outputs_dir=outputs, ttl_hours=48)
    assert report["ok"]
    assert report["freed_bytes"] > 0
    assert not old_p.exists() and new_p.exists()
    assert not old_e.exists() and new_e.exists()
    assert not (projects / "stale.part").exists()
    reasons = {d["reason"] for d in report["deleted"]}
    assert {"project", "export", "intermediate"} <= reasons


def test_ttl_zero_skips_auto_but_all_unused_deletes(tmp_path):
    projects, outputs = tmp_path / "projects", tmp_path / "outputs"
    _project(projects, "proj_old", 72)
    _touch(outputs / "old.mp4", 72, b"e" * 40)
    report = cache_gc.purge(projects_dir=projects, outputs_dir=outputs, ttl_hours=0)
    assert report["freed_bytes"] == 0
    assert (projects / "proj_old").exists()
    report = cache_gc.purge(projects_dir=projects, outputs_dir=outputs, ttl_hours=0, all_unused=True)
    assert not (projects / "proj_old").exists()
    assert not (outputs / "old.mp4").exists()
    assert report["freed_bytes"] > 0


def test_never_deletes_queue_jobs_or_running_project(tmp_path):
    projects, outputs = tmp_path / "projects", tmp_path / "outputs"
    running = _project(projects, "proj_run", 72)
    _project(projects, "proj_old", 72)
    qdir = projects / "_queue_files"
    qdir.mkdir()
    live = _touch(qdir / "live.mp4", 72, b"queued-file")
    (projects / "_jobs.json").write_text("{}", encoding="utf-8")
    (projects / "_queue.json").write_text(
        json.dumps({"items": [
            {"id": "q1", "status": "running", "project_id": "proj_run", "kind": "url", "source": "https://a.b/v"},
            {"id": "q2", "status": "queued", "kind": "file", "source": live.as_posix()},
        ]}),
        encoding="utf-8",
    )
    orphan = _touch(qdir / "orphan.mp4", 72, b"orphan-file")
    report = cache_gc.purge(projects_dir=projects, outputs_dir=outputs, ttl_hours=48)
    assert (projects / "_queue.json").exists()
    assert (projects / "_jobs.json").exists()
    assert running.exists()
    assert not (projects / "proj_old").exists()
    assert live.exists()
    assert not orphan.exists()
    skipped_names = {Path(s["path"]).name for s in report["skipped"]}
    assert "_queue.json" in skipped_names
    assert "proj_run" in skipped_names or running.name in skipped_names


def test_protect_context_and_dry_run(tmp_path):
    projects, outputs = tmp_path / "projects", tmp_path / "outputs"
    kept = _project(projects, "proj_kept", 72)
    doomed = _project(projects, "proj_gone", 72)
    with cache_gc.protecting("proj_kept"):
        report = cache_gc.purge(projects_dir=projects, outputs_dir=outputs, ttl_hours=48, dry_run=True)
    assert kept.exists() and doomed.exists()  # dry-run
    assert any(d["path"].endswith("proj_gone") for d in report["deleted"])
    report = cache_gc.purge(
        projects_dir=projects, outputs_dir=outputs, ttl_hours=48, protect=["proj_kept"]
    )
    assert kept.exists() and not doomed.exists()


def test_intermediates_inside_kept_project(tmp_path):
    projects, outputs = tmp_path / "projects", tmp_path / "outputs"
    pdir = _project(projects, "proj_keep", 1)
    stale = _touch(pdir / "chunk.part", 72, b"partial-chunk")
    fresh = _touch(pdir / "recent.part", 0.1, b"fresh-chunk")
    report = cache_gc.purge(projects_dir=projects, outputs_dir=outputs, ttl_hours=48)
    assert pdir.exists()
    assert not stale.exists() and fresh.exists()
    assert any(d["reason"] == "intermediate" for d in report["deleted"])


def test_disk_status_and_human_bytes(tmp_path):
    st = cache_gc.disk_status(tmp_path, min_free_mb=0)
    assert st["ok"] is True and st["free_mb"] >= 0 and st["total_bytes"] > 0
    st = cache_gc.disk_status(tmp_path, min_free_mb=10**12)
    assert st["ok"] is False
    assert cache_gc.human_bytes(0) == "0 B"
    assert "KB" in cache_gc.human_bytes(2048)
    assert "MB" in cache_gc.human_bytes(2 * 1024 * 1024)


def test_auto_purge_disabled_when_ttl_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIP_MASTER_CACHE_TTL_HOURS", "0")
    assert cache_gc.auto_purge() is None


def test_api_clean_and_low_disk_guard(client, tmp_path, monkeypatch):
    import server

    # Seed an expired export / project in the test data dir
    old = config.OUTPUTS_DIR / "stale_export.mp4"
    old.write_bytes(b"stale" * 30)
    os.utime(old, (time.time() - 72 * 3600, time.time() - 72 * 3600))

    status = client.get("/api/cache").json()
    assert "disk" in status and "ttl_hours" in status

    report = client.post("/api/cache/clean").json()
    assert report["ok"] is True
    assert "freed_human" in report
    assert not old.exists()

    # Low-disk guard refuses new jobs
    monkeypatch.setattr(server.cache_gc, "disk_status_for_app", lambda: {
        "ok": False, "free_mb": 10, "min_free_mb": 1024,
        "free_bytes": 10 * 1024 * 1024, "total_bytes": 100, "used_bytes": 90, "path": str(tmp_path),
    })
    r = client.post("/api/pull", json={"url": "https://example.com/v"})
    assert r.status_code == 507
    r = client.post("/api/queue", json={"urls": ["https://example.com/v"]})
    assert r.status_code == 507
    # Cleaning the cache must still be allowed when the disk is low
    assert client.post("/api/cache/clean").status_code == 200


def test_cli_clean_dry_run(tmp_path, monkeypatch, capsys):
    import clip_master_cli
    import pipeline

    monkeypatch.setattr(pipeline, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(pipeline, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    _project(tmp_path / "projects", "proj_old", 72)
    rc = clip_master_cli.main(["clean", "--dry-run", "--ttl", "48"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out.lower()
    assert (tmp_path / "projects" / "proj_old").exists()
