import time

import pytest

from batch_queue import BatchQueue


def make(tmp_path, runner=None, busy=None):
    runner = runner or (lambda item, progress: (progress("01 / PULL", 50, "half"), {"project_id": "p1", "output": "o.mp4"})[1])
    return BatchQueue(tmp_path / "q.json", tmp_path / "files", runner, busy)


def test_success_flow_and_persistence(tmp_path):
    q = make(tmp_path)
    it = q.add("url", "https://a.b/v1", options={"export_preset": "tiktok"})
    q.run_one(it["id"])
    done = q.get(it["id"])
    assert done["status"] == "done" and done["output"] == "o.mp4" and done["percent"] == 100
    q2 = make(tmp_path)  # restart
    assert q2.get(it["id"])["status"] == "done"


def test_running_item_requeued_after_restart(tmp_path):
    q = make(tmp_path)
    it = q.add("url", "https://a.b/v1")
    q.items[it["id"]]["status"] = "running"
    q._save()
    q2 = make(tmp_path)
    assert q2.get(it["id"])["status"] == "queued"


def test_error_then_retry(tmp_path):
    calls = {"n": 0}

    def runner(item, progress):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {"project_id": "p", "output": "x.mp4"}

    q = make(tmp_path, runner)
    it = q.add("url", "https://a.b/v1")
    q.run_one(it["id"])
    assert q.get(it["id"])["status"] == "error" and "boom" in q.get(it["id"])["error"]
    q.retry(it["id"])
    assert q.get(it["id"])["status"] == "queued"
    q.run_one(it["id"])
    assert q.get(it["id"])["status"] == "done" and q.get(it["id"])["attempts"] == 2


def test_retry_only_for_failed(tmp_path):
    q = make(tmp_path)
    it = q.add("url", "https://a.b/v1")
    with pytest.raises(ValueError):
        q.retry(it["id"])


def test_cancel_queued(tmp_path):
    q = make(tmp_path)
    it = q.add("url", "https://a.b/v1")
    assert q.cancel(it["id"])["status"] == "cancelled"


def test_cancel_running(tmp_path):
    def runner(item, progress):
        q.cancel(item["id"])
        progress("02", 10, "next tick")  # raises Cancelled
        return {}

    q = make(tmp_path, runner)
    it = q.add("url", "https://a.b/v1")
    q.run_one(it["id"])
    assert q.get(it["id"])["status"] == "cancelled"


def test_worker_runs_in_order_and_waits_when_busy(tmp_path):
    order = []
    state = {"busy": True}
    q = make(tmp_path, lambda item, progress: order.append(item["source"]) or {}, busy=lambda: state["busy"])
    q.add("url", "https://a.b/1")
    time.sleep(0.01)
    q.add("url", "https://a.b/2")
    q.start()
    time.sleep(0.3)
    assert order == []  # busy
    state["busy"] = False
    for _ in range(100):
        if len(order) == 2:
            break
        time.sleep(0.1)
    q.stop()
    assert order == ["https://a.b/1", "https://a.b/2"]


def test_remove_and_clear(tmp_path):
    q = make(tmp_path)
    a = q.add("url", "https://a.b/1")
    b = q.add("url", "https://a.b/2")
    q.run_one(a["id"])
    assert q.clear_finished() == 1
    q.remove(b["id"])
    assert q.list() == []


def test_uploaded_file_deleted_when_done(tmp_path):
    f = tmp_path / "files" / "x.mp4"
    f.parent.mkdir()
    f.write_bytes(b"x")
    q = make(tmp_path)
    it = q.add("file", str(f))
    q.run_one(it["id"])
    assert not f.exists()


# --- HTTP layer -------------------------------------------------------------
def test_api_queue_roundtrip(client, monkeypatch):
    import server
    monkeypatch.setattr(server.QUEUE, "busy", lambda: True)  # keep the real worker from starting any item
    r = client.post("/api/queue", json={"urls": ["https://a.b/v1", "https://a.b/v2"], "options": {"export_preset": "youtube_shorts", "translate_to": "fr"}})
    assert r.status_code == 200 and len(r.json()["added"]) == 2
    items = client.get("/api/queue").json()["items"]
    assert items[0]["options"]["export_preset"] == "youtube_shorts"
    iid = r.json()["added"][0]["id"]
    assert client.post(f"/api/queue/{iid}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/queue/{iid}/retry").json()["status"] == "queued"
    assert client.post("/api/queue/nope/cancel").status_code == 404
    assert client.post("/api/queue-clear").status_code == 200
    for it in client.get("/api/queue").json()["items"]:
        client.post(f"/api/queue/{it['id']}/cancel")
        client.delete(f"/api/queue/{it['id']}")


def test_api_queue_rejects_bad_url(client):
    assert client.post("/api/queue", json={"urls": ["--exec=x"]}).status_code == 400
    assert client.post("/api/queue", json={"urls": []}).status_code == 422
    assert client.post("/api/queue", json={"urls": ["https://a.b/v"], "options": {"export_preset": "x"}}).status_code == 422
