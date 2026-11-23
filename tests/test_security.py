import pytest

import pipeline


@pytest.mark.parametrize("bad", ["", "--exec=id", "-o /tmp/x", "file:///etc/passwd", "ftp://x/y", "https://a.b/c d",
                                 "https://a.b/;rm", "https://a.b/`id`", "javascript:alert(1)", "https://" + "a" * 3000])
def test_url_rejected(bad):
    with pytest.raises(ValueError):
        pipeline.validate_source_url(bad)


def test_url_accepted():
    assert pipeline.validate_source_url(" https://www.twitch.tv/videos/123456789?t=1h2m3s ").startswith("https://")


@pytest.mark.parametrize("v,ok", [("90", True), ("1:30", True), ("0:01:30.5", True), ("1;2", False), ("--x", False), ("a", False), ("1:2:3:4", False)])
def test_time_spec(v, ok):
    if ok:
        assert pipeline.validate_time_spec(v) == v
    else:
        with pytest.raises(ValueError):
            pipeline.validate_time_spec(v)


def test_pull_rejects_option_url(client):
    r = client.post("/api/pull", json={"url": "--exec=touch /tmp/pwned"})
    assert r.status_code == 200  # job is created, then fails validation inside the worker


def test_pull_failure_leaves_no_project(client):
    before = set(p.name for p in pipeline.PROJECTS_DIR.iterdir())
    with pytest.raises(ValueError):
        pipeline.stage_01_pull("ytdlp", url="--bad")
    assert set(p.name for p in pipeline.PROJECTS_DIR.iterdir()) == before


def test_csrf_guard(client):
    r = client.post("/api/pull", json={"url": "https://a.b/c"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/open-outputs", headers={"Origin": ""})
    assert r.status_code == 403


def test_project_id_traversal(client):
    for pid in ["..", "..%2f..%2fetc", "proj_../x", "x" * 80]:
        assert client.get(f"/api/projects/{pid}").status_code in (400, 404, 422)
    assert client.delete("/api/projects/..").status_code in (400, 404, 405)


def test_delete_output_traversal(client, tmp_path):
    victim = pipeline.OUTPUTS_DIR.parent / "victim.txt"
    victim.write_text("x")
    client.delete("/api/outputs/..%2Fvictim.txt")
    assert victim.exists()


def test_upload_limit(client, monkeypatch):
    import server
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 1024)
    r = client.post("/api/upload", files={"file": ("big.mp4", b"0" * 5000, "video/mp4")})
    assert r.status_code == 413
    assert not list(pipeline.PROJECTS_DIR.glob("upload_*"))


def test_upload_name_sanitised(client, monkeypatch):
    import server
    seen = {}
    monkeypatch.setattr(server, "_run_bg", lambda jid, fn, *a, **k: seen.setdefault("called", True))
    r = client.post("/api/upload", files={"file": ("../../evil name?.mp4", b"abc", "video/mp4")})
    assert r.status_code == 200 and seen["called"]
    for p in pipeline.PROJECTS_DIR.glob("upload_*"):
        assert p.parent == pipeline.PROJECTS_DIR and "/" not in p.name and "?" not in p.name
        p.unlink()
