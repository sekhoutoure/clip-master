"""Access control and network-safety helpers for CLIP MASTER.

* Network mode: when the server listens on something other than loopback it must have a token
  (``CLIP_MASTER_TOKEN``). The API accepts it as ``Authorization: Bearer <token>`` or ``X-Clip-Master-Token``;
  the web UI exchanges ``/?token=...`` once for an HttpOnly cookie.
* SSRF guard: ``check_public_host`` resolves a URL host and refuses private / loopback / link-local /
  reserved / multicast addresses (IPv4 and IPv6).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import socket
from urllib.parse import urlsplit

logger = logging.getLogger("clip_master.security")

COOKIE_NAME = "clip_master_token"
TOKEN_HEADER = "x-clip-master-token"
MIN_NETWORK_TOKEN_LEN = 16


def is_loopback_host(host: str | None) -> bool:
    h = (host or "").strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def check_startup(host: str, token: str) -> None:
    """Refuse to start on a non-loopback address without a strong enough token."""
    if is_loopback_host(host):
        return
    if not token:
        raise RuntimeError(
            f"Refusing to listen on {host!r} without CLIP_MASTER_TOKEN: the API would be open to the whole network.\n"
            f"Refus de démarrer sur {host!r} sans CLIP_MASTER_TOKEN : l'API serait ouverte à tout le réseau.\n"
            "  Set a token / Définis un jeton : python -c \"import secrets; print(secrets.token_urlsafe(24))\"\n"
            "  then / puis : CLIP_MASTER_TOKEN=<token> in .env  (or use CLIP_MASTER_HOST=127.0.0.1 / ou reste sur 127.0.0.1)"
        )
    if len(token) < MIN_NETWORK_TOKEN_LEN:
        raise RuntimeError(
            f"CLIP_MASTER_TOKEN is too short for network mode (minimum {MIN_NETWORK_TOKEN_LEN} characters).\n"
            f"CLIP_MASTER_TOKEN est trop court pour le mode réseau (minimum {MIN_NETWORK_TOKEN_LEN} caractères)."
        )


def cookie_value(token: str) -> str:
    """What the browser cookie holds: a digest, never the raw token."""
    return hashlib.sha256(("clip-master-cookie:" + token).encode("utf-8")).hexdigest()


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def token_from_headers(headers) -> str:
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (headers.get(TOKEN_HEADER) or "").strip()


def header_token_ok(headers, token: str) -> bool:
    given = token_from_headers(headers)
    return bool(token) and bool(given) and _eq(given, token)


def cookie_ok(cookies, token: str) -> bool:
    given = cookies.get(COOKIE_NAME) or ""
    return bool(token) and bool(given) and _eq(given, cookie_value(token))


def query_token_ok(given: str | None, token: str) -> bool:
    return bool(token) and bool(given) and _eq(given.strip(), token)


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CLIP MASTER</title><style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;display:grid;place-items:center;height:100vh;margin:0}
form{background:#1b1b1f;padding:28px;border-radius:12px;width:min(92vw,360px)}input,button{width:100%;box-sizing:border-box;padding:10px;margin-top:10px;border-radius:8px;border:1px solid #444;font-size:15px}
button{background:#ee1d6c;color:#fff;border:0;cursor:pointer}small{color:#999;display:block;margin-top:12px}.err{color:#ff7a90}</style></head>
<body><form method="post" action="/login"><h2>CLIP MASTER</h2><div>Access token / Jeton d'accès</div>
<input type="password" name="token" autocomplete="current-password" autofocus required>
<button type="submit">Open / Ouvrir</button>%ERR%<small>CLIP_MASTER_TOKEN</small></form></body></html>"""


def login_page(error: bool = False) -> str:
    msg = '<small class="err">Invalid token / Jeton invalide</small>' if error else ""
    return LOGIN_PAGE.replace("%ERR%", msg)


# --------------------------------------------------------------------------- SSRF


def _addr_is_public(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None and not ip.sixtofour.is_global:
            return False
        elif ip.teredo is not None:
            return False
    return bool(ip.is_global) and not (ip.is_multicast or ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved or ip.is_unspecified)


def _getaddrinfo(host: str):
    return socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)


def resolve_host_ips(host: str) -> list[ipaddress._BaseAddress]:
    """All addresses a host name (or numeric host, including 0x7f.1 / 2130706433 forms) resolves to."""
    h = host.strip("[]")
    try:
        return [ipaddress.ip_address(h)]
    except ValueError:
        pass
    infos = _getaddrinfo(h)
    out: list[ipaddress._BaseAddress] = []
    for info in infos:
        addr = info[4][0].split("%", 1)[0]
        try:
            out.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    return out


class BlockedURL(ValueError):
    """Raised by check_public_host; ``key`` is a messages.py catalog key."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def check_public_host(url: str, allow_private: bool = False) -> None:
    """Raise BlockedURL (a ValueError) when the URL targets a non-public address. DNS failures are not blocked here
    (the downloader fails by itself on an unresolvable name)."""
    if allow_private:
        return
    host = urlsplit(url).hostname
    if not host:
        raise BlockedURL("ssrf.no_host")
    try:
        ips = resolve_host_ips(host)
    except (socket.gaierror, UnicodeError, OSError):
        return
    for ip in ips:
        if not _addr_is_public(ip):
            logger.warning("Blocked source URL pointing at non-public address %s (host %r)", ip, host)
            raise BlockedURL("ssrf.blocked")
