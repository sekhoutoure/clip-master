"""Backend messages in French and English (same dictionaries idea as ``static/i18n``).

The language of the current request comes from the ``X-Lang`` header (sent by the web UI) or ``Accept-Language``;
background jobs keep the language of the request that started them. ``CLIP_MASTER_LANG`` sets the default
(``en`` when unset). Use ``t(key, **vars)`` for text, or raise ``LocalizedError`` / ``LocalizedRuntimeError``:
``str(exc)`` is rendered in the active language when it is read.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any

SUPPORTED = ("fr", "en")
DEFAULT_LANG = (os.getenv("CLIP_MASTER_LANG") or "en").strip().lower()
if DEFAULT_LANG not in SUPPORTED:
    DEFAULT_LANG = "en"

_lang: ContextVar[str] = ContextVar("clip_master_lang", default=DEFAULT_LANG)

CATALOG: dict[str, dict[str, str]] = {
    "server.restarted": {"fr": "Serveur redémarré pendant l'exécution", "en": "Server restarted while the job was running"},
    "server.disk_low": {
        "fr": "Espace disque insuffisant ({free_mb} Mo libres, minimum {min_free_mb} Mo). Vide le cache ou libère de l'espace.",
        "en": "Not enough disk space ({free_mb} MB free, minimum {min_free_mb} MB). Clear the cache or free some space.",
    },
    "server.too_many_jobs": {
        "fr": "Trop de tâches en cours ({running}/{max}). Attends la fin d'un traitement.",
        "en": "Too many jobs running ({running}/{max}). Wait for the current one to finish.",
    },
    "server.bad_options": {"fr": "Options invalides", "en": "Invalid options"},
    "server.too_large": {"fr": "Fichier trop volumineux (limite {limit_mb} Mo)", "en": "File too large (limit {limit_mb} MB)"},
    "server.item_not_found": {"fr": "Élément introuvable", "en": "Item not found"},
    "server.job_not_found": {"fr": "Tâche introuvable", "en": "Job not found"},
    "server.project_not_found": {"fr": "Projet introuvable", "en": "Project not found"},
    "server.invalid_filename": {"fr": "Nom de fichier invalide", "en": "Invalid filename"},
    "server.invalid_upload_filename": {"fr": "Nom de fichier d'envoi invalide", "en": "Invalid upload filename"},
    "server.invalid_project_id": {"fr": "Identifiant de projet invalide", "en": "Invalid project_id"},
    "server.media_not_found": {"fr": "Média introuvable", "en": "Media not found"},
    "server.export_not_found": {"fr": "Export introuvable", "en": "Export not found"},
    "server.local_only": {"fr": "Disponible uniquement depuis la machine locale", "en": "Only available from the local machine"},
    "server.srt_missing": {
        "fr": "SRT pas encore généré. Lance d'abord 04 / CAPTION.",
        "en": "SRT not generated yet. Run 04 / CAPTION first.",
    },
    "auth.required": {
        "fr": "Authentification requise : envoie CLIP_MASTER_TOKEN (Authorization: Bearer ou X-Clip-Master-Token).",
        "en": "Authentication required: send CLIP_MASTER_TOKEN (Authorization: Bearer or X-Clip-Master-Token).",
    },
    "csrf.cross_site": {"fr": "Origine inter-sites refusée (protection CSRF)", "en": "Cross-site origin rejected (CSRF protection)"},
    "csrf.cross_site_referer": {"fr": "Referer inter-sites refusé (protection CSRF)", "en": "Cross-site referer rejected (CSRF protection)"},
    "csrf.missing": {
        "fr": "En-tête Origin/Referer manquant (protection CSRF). Pour les scripts : envoie Origin ou un jeton Bearer.",
        "en": "Missing Origin/Referer header (CSRF protection). For scripts: send an Origin header or a Bearer token.",
    },
    "pipeline.bad_project_id": {"fr": "Identifiant de projet invalide : {value}", "en": "Invalid project id: {value}"},
    "pipeline.project_outside": {"fr": "Chemin de projet hors confinement : {value}", "en": "Project path escapes the projects folder: {value}"},
    "pipeline.bad_url": {
        "fr": "URL invalide : utilise un lien http(s) complet, sans espaces.",
        "en": "Invalid URL: use a complete http(s) link without spaces.",
    },
    "pipeline.bad_time": {
        "fr": "Horodatage invalide : {value} (attendu SS, MM:SS ou HH:MM:SS).",
        "en": "Invalid timestamp: {value} (expected SS, MM:SS or HH:MM:SS).",
    },
    "pipeline.need_source": {"fr": "Fournis une URL ou un fichier vidéo local.", "en": "Provide a URL or a local video file."},
    "pipeline.no_captions": {
        "fr": "Aucun sous-titre à traduire : lance d'abord l'étape CAPTION.",
        "en": "No captions to translate: run the CAPTION stage first.",
    },
    "pipeline.project_missing": {"fr": "Projet introuvable : {value}", "en": "Project not found: {value}"},
    "pipeline.qc_failed": {
        "fr": "QC échoué ({why}). Fichier conservé comme {name} (non validé).",
        "en": "QC failed ({why}). File kept as {name} (not validated).",
    },
    "pipeline.export_failed": {"fr": "Export 9:16 échoué : {err}", "en": "9:16 export failed: {err}"},
    "queue.full": {"fr": "File d'attente pleine ({max}). Supprime les éléments terminés.", "en": "Queue is full ({max}). Remove finished items."},
    "queue.retry_state": {
        "fr": "Seuls les éléments en erreur ou annulés peuvent être relancés.",
        "en": "Only failed or cancelled items can be retried.",
    },
    "queue.source_gone": {"fr": "Le fichier source n'existe plus : ajoute-le à nouveau.", "en": "The source file no longer exists: add it again."},
    "queue.cancel_first": {"fr": "Annule l'élément avant de le supprimer.", "en": "Cancel the item before removing it."},
    "ssrf.blocked": {
        "fr": "URL refusée (adresse privée ou locale). Définis CLIP_MASTER_ALLOW_PRIVATE_URLS=1 pour l'autoriser.",
        "en": "URL refused (private or local address). Set CLIP_MASTER_ALLOW_PRIVATE_URLS=1 to allow it.",
    },
    "ssrf.no_host": {"fr": "URL invalide : pas d'hôte.", "en": "Invalid URL: no host."},
}


def normalize(lang: str | None) -> str | None:
    if not lang:
        return None
    base = lang.strip().lower().replace("_", "-").split("-", 1)[0]
    return base if base in SUPPORTED else None


def lang_from_headers(headers) -> str:
    """``X-Lang`` (set by the UI) first, then the first usable ``Accept-Language`` tag, then the default."""
    explicit = normalize(headers.get("x-lang"))
    if explicit:
        return explicit
    for part in (headers.get("accept-language") or "").split(","):
        n = normalize(part.split(";", 1)[0])
        if n:
            return n
    return DEFAULT_LANG


def set_lang(lang: str | None):
    return _lang.set(normalize(lang) or DEFAULT_LANG)


def get_lang() -> str:
    return _lang.get()


def t(key: str, lang: str | None = None, **params: Any) -> str:
    entry = CATALOG.get(key)
    if entry is None:
        return key
    text = entry.get(normalize(lang) or get_lang()) or entry["en"]
    try:
        return text.format(**params) if params else text
    except (KeyError, IndexError):
        return text


class _Localized:
    key: str
    params: dict[str, Any]

    def _init_loc(self, key: str, params: dict[str, Any]) -> None:
        self.key, self.params = key, params

    def localized(self, lang: str | None = None) -> str:
        return t(self.key, lang, **self.params)

    def __str__(self) -> str:
        return self.localized()


class LocalizedError(_Localized, ValueError):
    def __init__(self, key: str, **params: Any):
        ValueError.__init__(self, key)
        self._init_loc(key, params)


class LocalizedRuntimeError(_Localized, RuntimeError):
    def __init__(self, key: str, **params: Any):
        RuntimeError.__init__(self, key)
        self._init_loc(key, params)
