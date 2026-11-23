"""Persistent batch queue: several VOD links / files processed one after another.

Items are stored in ``projects/_queue.json`` so the queue survives a restart (an item that was running when
the server stopped is put back in the queue). One worker thread runs the items sequentially; it waits
while a manual job is running so the machine is never asked to do two heavy renders at once.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

MAX_ITEMS = 200

Runner = Callable[[dict[str, Any], Callable[[str, int, str, list], None]], dict[str, Any]]


class Cancelled(Exception):
    """Raised from the progress callback to abort a running item at its next progress tick."""


class BatchQueue:
    def __init__(self, store: Path, files_dir: Path, runner: Runner, busy: Callable[[], bool] | None = None):
        self.store = store
        self.files_dir = files_dir
        self.runner = runner
        self.busy = busy or (lambda: False)
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cancel: set[str] = set()
        self.items: dict[str, dict[str, Any]] = {}
        self._load()

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
        except Exception:
            return
        for it in data.get("items", []):
            if it.get("status") == "running":
                it["status"] = "queued"
                it["message"] = "Repris après redémarrage"
                it["percent"] = 0
            self.items[it["id"]] = it

    def _save(self) -> None:
        with self._lock:
            try:
                self.store.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.store.with_suffix(".tmp")
                tmp.write_text(json.dumps({"items": list(self.items.values())}, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, self.store)
            except OSError:
                pass

    # -- public API ----------------------------------------------------------
    def add(self, kind: str, source: str, label: str | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
        if kind not in ("url", "file"):
            raise ValueError("kind must be 'url' or 'file'")
        with self._lock:
            if len(self.items) >= MAX_ITEMS:
                raise ValueError(f"File d'attente pleine ({MAX_ITEMS}). Supprime les éléments terminés.")
            now = time.time()
            item = {
                "id": uuid.uuid4().hex[:10], "kind": kind, "source": source,
                "label": (label or Path(source).name or source)[:120], "options": options or {},
                "status": "queued", "stage": "", "percent": 0, "message": "En attente", "error": None,
                "project_id": None, "output": None, "attempts": 0, "created_at": now, "updated_at": now,
            }
            self.items[item["id"]] = item
            self._save()
        self._wake.set()
        return dict(item)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted((dict(i) for i in self.items.values()), key=lambda i: i["created_at"])

    def is_running(self) -> bool:
        with self._lock:
            return any(i["status"] == "running" for i in self.items.values())

    def get(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            if item_id not in self.items:
                raise KeyError(item_id)
            return dict(self.items[item_id])

    def cancel(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            it = self.items[item_id]
            if it["status"] == "queued":
                self._finish(it, "cancelled", message="Annulé")
            elif it["status"] == "running":
                self._cancel.add(item_id)
                it["message"] = "Annulation en cours…"
            self._save()
            return dict(it)

    def retry(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            it = self.items[item_id]
            if it["status"] not in ("error", "cancelled"):
                raise ValueError("Seuls les éléments en erreur ou annulés peuvent être relancés.")
            if it["kind"] == "file" and not Path(it["source"]).exists():
                raise ValueError("Le fichier source n'existe plus : ajoute-le à nouveau.")
            it.update(status="queued", stage="", percent=0, message="En attente", error=None, updated_at=time.time())
            self._cancel.discard(item_id)
            self._save()
        self._wake.set()
        return dict(it)

    def remove(self, item_id: str) -> None:
        with self._lock:
            it = self.items[item_id]
            if it["status"] == "running":
                raise ValueError("Annule l'élément avant de le supprimer.")
            self._drop_file(it)
            del self.items[item_id]
            self._save()

    def clear_finished(self) -> int:
        with self._lock:
            ids = [i for i, it in self.items.items() if it["status"] in ("done", "cancelled")]
            for i in ids:
                self._drop_file(self.items[i])
                del self.items[i]
            self._save()
            return len(ids)

    # -- worker --------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="batch-queue", daemon=True)
        self._thread.start()
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _next_queued(self) -> dict[str, Any] | None:
        with self._lock:
            queued = [i for i in self.items.values() if i["status"] == "queued"]
            return min(queued, key=lambda i: i["created_at"]) if queued else None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.busy():
                self._stop.wait(2.0)
                continue
            it = self._next_queued()
            if it is None:
                self._wake.wait(5.0)
                self._wake.clear()
                continue
            self.run_one(it["id"])

    def run_one(self, item_id: str) -> None:
        """Run a single item synchronously (used by the worker and by tests)."""
        with self._lock:
            it = self.items[item_id]
            it.update(status="running", attempts=it["attempts"] + 1, error=None, updated_at=time.time(), message="Démarrage…")
            self._save()
            snapshot = dict(it)

        def progress(stage: str, percent: int, message: str, _repos: list | None = None) -> None:
            with self._lock:
                if item_id in self._cancel:
                    raise Cancelled()
                cur = self.items.get(item_id)
                if cur:
                    cur.update(stage=stage, percent=int(percent), message=message, updated_at=time.time())
                    self._save()

        try:
            result = self.runner(snapshot, progress)
            with self._lock:
                if item_id in self._cancel:
                    raise Cancelled()
                it = self.items[item_id]
                it["project_id"] = result.get("project_id")
                it["output"] = result.get("output")
                self._finish(it, "done", message="Terminé", percent=100)
        except Cancelled:
            with self._lock:
                self._finish(self.items[item_id], "cancelled", message="Annulé")
        except Exception as exc:  # noqa: BLE001 - report any failure on the item, keep the queue going
            with self._lock:
                self._finish(self.items[item_id], "error", message="Échec", error=str(exc)[:500])
        finally:
            with self._lock:
                self._cancel.discard(item_id)
                self._save()

    # -- helpers -------------------------------------------------------------
    def _finish(self, it: dict[str, Any], status: str, message: str, percent: int | None = None, error: str | None = None) -> None:
        it.update(status=status, message=message, error=error, updated_at=time.time())
        if percent is not None:
            it["percent"] = percent
        if status in ("done", "cancelled"):
            self._drop_file(it)

    def _drop_file(self, it: dict[str, Any]) -> None:
        if it.get("kind") == "file":
            p = Path(it["source"])
            try:
                if p.resolve().is_relative_to(self.files_dir.resolve()):
                    p.unlink(missing_ok=True)
            except OSError:
                pass
