"""Optional caption profanity filter (FR/EN).

Words stay in the audio; they are removed (or masked) only from burned-in captions.
Pure Python, no heavy imports. Enable it with the `censor_captions` option of the tracked renderer.
"""
from __future__ import annotations

import re
import unicodedata

_SWEARS_EXACT = {
    "putain", "ptn", "pute", "putes", "merde", "merdes", "merdique", "bordel", "con", "cons", "conne", "connes",
    "connard", "connards", "connasse", "connasses", "salope", "salopes", "salaud", "salauds", "enculé", "encule",
    "enculés", "enculer", "encul", "nique", "niquer", "niqué", "nik", "ntm", "fdp", "tg", "batard", "bâtard",
    "batards", "bâtards", "bite", "bites", "couille", "couilles", "foutre", "chier", "enfoiré", "enfoirés",
    "enfoire", "pd", "tapette", "zgeg", "teub", "fuck", "fucking", "fucked", "shit", "bitch", "bitches",
    "motherfucker", "wtf", "damn", "dick", "pussy", "asshole", "bastard", "cul", "trouduc", "bouffon",
    "abruti", "débile", "debile", "mongol", "gogol", "triso", "attardé", "pétasse", "petasse", "chienne",
}
_SWEAR_PREFIXES = ("putai", "encul", "niqu", "connar", "connas", "salop", "fuck", "motherf", "enfoir", "bâtar", "batar")


def _norm_token(tok: str) -> str:
    t = tok.lower().strip("'’-.,!?…:;\"«»“” ")
    t = re.sub(r"^(?:j|l|d|t|m|s|n|qu)['’]", "", t)   # l'enculé -> enculé
    return t


def is_swear(tok: str) -> bool:
    t = _norm_token(tok)
    if not t:
        return False
    plain = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    return t in _SWEARS_EXACT or plain in _SWEARS_EXACT or t.startswith(_SWEAR_PREFIXES) or plain.startswith(_SWEAR_PREFIXES)


def censor_word(tok: str, mode: str = "remove") -> str:
    if not is_swear(tok):
        return tok
    if mode == "mask":
        core = re.sub(r"[^\wÀ-ÿ]", "", tok)
        return (core[:1] + "*" * max(2, len(core) - 1)) if core else ""
    return ""


def censor_text(text: str, mode: str = "remove") -> str:
    out = [censor_word(t, mode) for t in (text or "").split()]
    return re.sub(r"\s+", " ", " ".join(t for t in out if t)).strip()
