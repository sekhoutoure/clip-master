"""Token authentication (network mode), startup guard, per-request language, CORS."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import security

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "t" * 12 + "okenTOKEN-1234"
ORIGIN = {"Origin": "http://127.0.0.1:7866"}


# ------------------------------------------------------------------ startup guard
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.2"])
def test_loopback_needs_no_token(host):
    security.check_startup(host, "")


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "myhost.lan"])
def test_network_host_without_token_refused(host):
    with pytest.raises(RuntimeError) as e:
        security.check_startup(host, "")
    assert "CLIP_MASTER_TOKEN" in str(e.value)


def test_network_host_short_token_refused():
    with pytest.raises(RuntimeError):
        security.check_startup("0.0.0.0", "short")
    security.check_startup("0.0.0.0", TOKEN)


@pytest.mark.parametrize("host", ["0.0.0.0", "::"])
def test_server_refuses_to_start_without_token(host, tmp_path):
    env = {**os.environ, "CLIP_MASTER_HOST": host, "CLIP_MASTER_TOKEN": "", "CLIP_MASTER_DATA_DIR": str(tmp_path)}
    r = subprocess.run([sys.executable, "-c", "import server"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0
    assert "CLIP_MASTER_TOKEN" in r.stderr


def test_server_starts_in_network_mode_with_token(tmp_path):
    env = {**os.environ, "CLIP_MASTER_HOST": "0.0.0.0", "CLIP_MASTER_TOKEN": TOKEN, "CLIP_MASTER_DATA_DIR": str(tmp_path)}
    r = subprocess.run([sys.executable, "-c", "import server; print(server.NETWORK_MODE, server.AUTH_REQUIRED)"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("True True")


# ------------------------------------------------------------------ HTTP behaviour
@pytest.fixture()
def secured(monkeypatch):
    import server
    monkeypatch.setattr(server, "TOKEN", TOKEN)
    monkeypatch.setattr(server, "AUTH_REQUIRED", True)
    with TestClient(server.app, base_url="http://127.0.0.1:7866") as c:
        yield c


def test_no_token_is_401_on_api_media_and_static(secured):
    for path in ["/api/projects", "/api/mode", "/api/queue", "/api/cache", "/media/outputs/x.mp4", "/static/app.js", "/api/jobs/abc"]:
        r = secured.get(path)
        assert r.status_code == 401, path
        assert r.headers["www-authenticate"] == "Bearer"
    assert secured.post("/api/pull", json={"url": "https://a.b/c"}, headers=ORIGIN).status_code == 401
    assert secured.delete("/api/projects/abc", headers=ORIGIN).status_code == 401


def test_wrong_token_rejected(secured):
    assert secured.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert secured.get("/api/projects", headers={"X-Clip-Master-Token": TOKEN + "x"}).status_code == 401
    assert secured.get("/api/projects", headers={"Authorization": TOKEN}).status_code == 401  # missing "Bearer"


def test_bearer_and_custom_header_accepted(secured):
    assert secured.get("/api/projects", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert secured.get("/api/projects", headers={"X-Clip-Master-Token": TOKEN}).status_code == 200


def test_health_stays_open_for_the_launcher(secured):
    assert secured.get("/api/health").status_code == 200


def test_scripts_with_token_do_not_need_origin(secured):
    r = secured.post("/api/queue-clear", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_cross_site_origin_still_rejected_with_token(secured):
    r = secured.post("/api/queue-clear", headers={"X-Clip-Master-Token": TOKEN, "Origin": "https://evil.example"})
    assert r.status_code == 200  # header token proves it is not a browser CSRF; origin is irrelevant
    # but the cookie path (browser) must keep the Origin check:
    secured.get(f"/?token={TOKEN}", follow_redirects=False)
    assert secured.post("/api/queue-clear", headers={"Origin": "https://evil.example"}).status_code == 403
    assert secured.post("/api/queue-clear").status_code == 403
    assert secured.post("/api/queue-clear", headers=ORIGIN).status_code == 200


def test_root_shows_login_then_cookie_flow(secured):
    r = secured.get("/")
    assert r.status_code == 200 and "<form" in r.text and "CLIP MASTER" in r.text
    assert "projects" not in r.text.lower().replace("clip master", "")

    bad = secured.get("/?token=wrong", follow_redirects=False)
    assert bad.status_code == 401 and "Invalid token" in bad.text
    assert security.COOKIE_NAME not in bad.headers.get("set-cookie", "")

    ok = secured.get(f"/?token={TOKEN}", follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/"
    cookie = ok.headers["set-cookie"]
    assert security.COOKIE_NAME in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert TOKEN not in cookie  # the cookie holds a digest, not the token

    # the cookie is now in the client jar: UI, API and media work without any header
    assert secured.get("/").status_code == 200 and "<form" not in secured.get("/").text
    assert secured.get("/api/projects").status_code == 200
    assert secured.get("/static/app.js").status_code == 200


def test_login_form_posts_the_token(secured, monkeypatch):
    import server
    assert 'method="post" action="/login"' in secured.get("/").text
    monkeypatch.setattr(server.asyncio, "sleep", lambda s: _noop())
    bad = secured.post("/login", data={"token": "wrong"}, follow_redirects=False)
    assert bad.status_code == 401 and "Invalid token" in bad.text and security.COOKIE_NAME not in bad.headers.get("set-cookie", "")
    assert secured.post("/login", data={}, follow_redirects=False).status_code == 401
    ok = secured.post("/login", data={"token": TOKEN}, follow_redirects=False)
    assert ok.status_code == 303 and security.COOKIE_NAME in ok.headers["set-cookie"] and TOKEN not in ok.headers["set-cookie"]
    assert secured.get("/api/projects").status_code == 200  # cookie jar now authenticated


async def _noop():
    return None


def test_query_token_is_only_honoured_on_root(secured):
    assert secured.get(f"/api/projects?token={TOKEN}").status_code == 401


def test_unauthorized_message_follows_language(secured):
    en = secured.get("/api/projects", headers={"X-Lang": "en"}).json()["detail"]
    fr = secured.get("/api/projects", headers={"X-Lang": "fr"}).json()["detail"]
    assert "Authentication required" in en and "Authentification requise" in fr
    fr2 = secured.get("/api/projects", headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}).json()["detail"]
    assert fr2 == fr


def test_loopback_without_token_stays_open(client):
    import server
    assert server.AUTH_REQUIRED is False
    assert client.get("/api/projects").status_code == 200


# ------------------------------------------------------------------ CSRF origin in network mode
def _request(host, scheme="http"):
    from starlette.requests import Request
    return Request({"type": "http", "headers": [(b"host", host.encode())], "scheme": scheme, "method": "POST", "path": "/", "query_string": b""})


def test_origin_matches_host_only_in_network_mode(monkeypatch):
    import server
    req = _request("192.168.1.20:7866")
    monkeypatch.setattr(server, "NETWORK_MODE", False)
    assert server._origin_allowed(req, "http://192.168.1.20:7866") is False
    assert server._origin_allowed(req, "http://127.0.0.1:7866") is True
    monkeypatch.setattr(server, "NETWORK_MODE", True)
    assert server._origin_allowed(req, "http://192.168.1.20:7866") is True
    assert server._origin_allowed(req, "http://evil.example") is False


# ------------------------------------------------------------------ CORS
def test_cors_preflight_is_narrow(client):
    r = client.options("/api/pull", headers={
        "Origin": "http://127.0.0.1:7866", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,authorization",
    })
    assert r.status_code == 200
    assert "access-control-allow-credentials" not in r.headers
    assert "PUT" not in r.headers["access-control-allow-methods"]
    r = client.options("/api/pull", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/api/health", headers={"Origin": "http://127.0.0.1:7866"})
    assert "access-control-allow-credentials" not in r.headers
