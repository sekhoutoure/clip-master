"""Bilingual backend messages and logging instead of silent failures."""
import logging

import messages


def test_catalog_is_complete_and_formats():
    for key, entry in messages.CATALOG.items():
        assert set(entry) == {"fr", "en"}, key
        assert entry["fr"].strip() and entry["en"].strip(), key
        # same placeholders in both languages
        import string
        names = lambda s: {f for _, f, _, _ in string.Formatter().parse(s) if f}  # noqa: E731
        assert names(entry["fr"]) == names(entry["en"]), key


def test_lang_resolution():
    assert messages.lang_from_headers({"x-lang": "fr"}) == "fr"
    assert messages.lang_from_headers({"x-lang": "EN", "accept-language": "fr"}) == "en"
    assert messages.lang_from_headers({"accept-language": "de;q=0.9, fr-CA;q=0.8"}) == "fr"
    assert messages.lang_from_headers({"accept-language": "de"}) == messages.DEFAULT_LANG
    assert messages.lang_from_headers({}) == messages.DEFAULT_LANG


def test_localized_error_follows_active_language():
    err = messages.LocalizedError("pipeline.bad_time", value="'x'")
    assert isinstance(err, ValueError)
    assert "Invalid timestamp" in err.localized("en") and "Horodatage invalide" in err.localized("fr")
    token = messages.set_lang("fr")
    try:
        assert "Horodatage invalide" in str(err)
    finally:
        messages._lang.reset(token)


def test_api_errors_follow_x_lang(client):
    en = client.get("/api/jobs/nope", headers={"X-Lang": "en"}).json()["detail"]
    fr = client.get("/api/jobs/nope", headers={"X-Lang": "fr"}).json()["detail"]
    assert en != fr and "not found" in en.lower() and "introuvable" in fr.lower()
    fr = client.post("/api/pull", json={"url": "x"}, headers={"X-Lang": "fr", "Origin": "https://evil.example"}).json()["detail"]
    assert "inter-sites" in fr
    en = client.post("/api/queue", json={"urls": ["--x"]}, headers={"X-Lang": "en"}).json()["detail"]
    assert "Invalid URL" in en
    fr = client.post("/api/queue", json={"urls": ["--x"]}, headers={"X-Lang": "fr"}).json()["detail"]
    assert "URL invalide" in fr


def test_job_error_uses_language_of_the_request(client, monkeypatch):
    import time
    import server
    r = client.post("/api/pull", json={"url": "--bad"}, headers={"X-Lang": "en"})
    jid = r.json()["job_id"]
    for _ in range(50):
        job = client.get(f"/api/jobs/{jid}").json()
        if job["status"] != "running":
            break
        time.sleep(0.1)
    assert job["status"] == "error" and "Invalid URL" in job["error"]
    assert job["lang"] == "en"
    server.JOBS.pop(jid, None)


def test_restart_message_localised():
    assert "Server restarted" in messages.t("server.restarted", "en") and "redémarré" in messages.t("server.restarted", "fr")


def test_delete_output_failure_is_logged_not_swallowed(client, monkeypatch, caplog):
    import server
    monkeypatch.setattr(server, "load_project", lambda pid: (_ for _ in ()).throw(RuntimeError("boom")))
    with caplog.at_level(logging.WARNING, logger="clip_master.server"):
        r = client.delete("/api/outputs/nothing.mp4?project_id=proj_abc123")
    assert r.status_code == 200 and r.json()["project_updated"] is False
    assert any("could not be updated" in rec.message and rec.exc_info for rec in caplog.records)


def test_loggers_are_module_scoped():
    import cache_gc, llm, pipeline, server, tracked_render  # noqa: E401
    from stages import ship
    for mod in (cache_gc, llm, pipeline, server, tracked_render, ship):
        assert mod.logger.name.startswith("clip_master.")
    assert logging.getLogger().handlers  # basicConfig ran in server


def test_no_silent_except_pass_left():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name in ("server.py", "pipeline.py", "stages/ship.py", "tracked_render.py", "cache_gc.py", "llm.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert not re.search(r"except Exception[^\n]*:\s*\n\s*pass\b", src), name
