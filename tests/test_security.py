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
    def dirs():  # directories only: a background job may be rewriting _jobs.json(.tmp) at the same moment
        return {p.name for p in pipeline.PROJECTS_DIR.iterdir() if p.is_dir()}

    before = dirs()
    with pytest.raises(ValueError):
        pipeline.stage_01_pull("ytdlp", url="--bad")
    assert dirs() == before


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


def test_host_header_allowlist(client):
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"Host": "localhost:7866"}).status_code == 200


def test_media_traversal_blocked(client, project):
    pid = project["id"]
    assert client.get(f"/media/projects/{pid}/source.mp4").status_code == 200
    for sub in ["../../_jobs.json", "..%2f..%2f_jobs.json", "%2e%2e/%2e%2e/_jobs.json"]:
        assert client.get(f"/media/projects/{pid}/{sub}").status_code in (400, 404)
    assert client.get("/media/projects/_queue_files/x").status_code in (400, 404)
    assert client.get("/media/outputs/..%2fconftest.py").status_code == 404


# ---------------------------------------------------------------- more traversal / upload coverage
@pytest.mark.parametrize("sub", [
    "../project.json", "..%2fproject.json", "..%5c..%5cproject.json", "%2e%2e%2f%2e%2e%2f_jobs.json",
    "....//....//_jobs.json", "/etc/passwd", "%2fetc%2fpasswd", "a/../../../_jobs.json", "..%252f..%252f_jobs.json", "source.mp4%00.png",
])
def test_media_traversal_variants(client, project, sub):
    pid = project["id"]
    r = client.get(f"/media/projects/{pid}/{sub}")
    assert r.status_code in (400, 404, 422)
    assert b"_jobs" not in r.content[:50] and b"root:" not in r.content


def test_media_traversal_via_project_id(client):
    for pid in ["..", "%2e%2e", "..%2f", "a/../..", ".hidden_x", "x" * 60]:
        assert client.get(f"/media/projects/{pid}/project.json").status_code in (400, 404, 422)


def test_media_outputs_cannot_leave_folder(client):
    for name in ["..%2fconftest.py", "..%5cconftest.py", "%2e%2e%2fconftest.py", "../../etc/passwd", "%2fetc%2fpasswd"]:
        assert client.get(f"/media/outputs/{name}").status_code in (400, 404, 422)


def test_media_symlink_escape_blocked(client, project):
    secret = pipeline.PROJECTS_DIR.parent / "outside_secret.txt"
    secret.write_text("TOP-SECRET")
    link = pipeline.PROJECTS_DIR / project["id"] / "leak.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    try:
        r = client.get(f"/media/projects/{project['id']}/leak.txt")
        assert r.status_code == 404 and b"TOP-SECRET" not in r.content
    finally:
        link.unlink(missing_ok=True)
        secret.unlink(missing_ok=True)


def test_upload_exactly_at_limit_is_accepted_and_one_over_is_not(client, monkeypatch):
    import server
    server.JOBS.clear()
    seen = {}
    monkeypatch.setattr(server, "_run_bg", lambda jid, fn, *a, **k: seen.setdefault("called", True))
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 2048)
    assert client.post("/api/upload", files={"file": ("ok.mp4", b"0" * 2048, "video/mp4")}).status_code == 200
    server.JOBS.clear()  # the mocked worker never finishes its job
    assert client.post("/api/upload", files={"file": ("big.mp4", b"0" * 2049, "video/mp4")}).status_code == 413
    for p in pipeline.PROJECTS_DIR.glob("upload_*"):
        p.unlink()


def test_upload_limit_error_message_is_bilingual(client, monkeypatch):
    import server
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 10)
    en = client.post("/api/upload", files={"file": ("big.mp4", b"0" * 100, "video/mp4")}, headers={"X-Lang": "en"}).json()["detail"]
    fr = client.post("/api/upload", files={"file": ("big.mp4", b"0" * 100, "video/mp4")}, headers={"X-Lang": "fr"}).json()["detail"]
    assert "too large" in en and "trop volumineux" in fr


def test_queue_upload_oversize_leaves_no_file(client, monkeypatch):
    import server
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 1024)
    before = set(server.QUEUE_FILES.glob("*")) if server.QUEUE_FILES.exists() else set()
    r = client.post("/api/queue/upload", files={"file": ("big.mp4", b"0" * 5000, "video/mp4")}, data={"options": "{}"})
    assert r.status_code == 413
    after = set(server.QUEUE_FILES.glob("*")) if server.QUEUE_FILES.exists() else set()
    assert after == before


def test_upload_filename_cannot_escape(client, monkeypatch):
    import server
    monkeypatch.setattr(server, "_run_bg", lambda *a, **k: None)
    server.JOBS.clear()
    r = client.post("/api/upload", files={"file": ("..\\..\\evil.mp4", b"abc", "video/mp4")})
    assert r.status_code in (200, 400)
    assert not (pipeline.PROJECTS_DIR.parent / "evil.mp4").exists()
    server.JOBS.clear()
    for p in pipeline.PROJECTS_DIR.glob("upload_*"):
        p.unlink()
