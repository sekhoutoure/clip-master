"""SSRF guard in validate_source_url."""
import socket

import pytest

import pipeline
import security


def _real_dns(monkeypatch):
    monkeypatch.setattr(security, "_getaddrinfo", lambda h: socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP))


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://127.0.0.1:8080/x", "http://127.1/", "http://0x7f.0.0.1/", "http://2130706433/",
    "http://0.0.0.0/", "http://10.0.0.5/v", "http://172.16.3.4/v", "http://192.168.1.10/v", "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/", "http://224.0.0.1/", "http://240.0.0.1/", "http://255.255.255.255/",
    "http://[::1]/", "http://[::]/", "http://[fe80::1]/", "http://[fd00::1]/", "http://[fc00::5]/", "http://[ff02::1]/",
    "http://[::ffff:127.0.0.1]/", "http://[::ffff:10.0.0.1]/", "http://[::ffff:169.254.169.254]/",
    "https://user:pw@192.168.0.1/x", "http://localhost/", "http://localhost:7866/api/projects",
])
def test_private_targets_rejected(url, monkeypatch):
    _real_dns(monkeypatch)
    with pytest.raises(ValueError) as e:
        pipeline.validate_source_url(url)
    assert "refus" in str(e.value).lower() or "refused" in str(e.value).lower()


def test_dns_name_resolving_to_private_rejected(monkeypatch):
    monkeypatch.setattr(security, "_getaddrinfo", lambda h: [(2, 1, 6, "", ("10.1.2.3", 0))])
    with pytest.raises(ValueError):
        pipeline.validate_source_url("https://internal.corp.example/video")


def test_dns_rebinding_style_mixed_answers_rejected(monkeypatch):
    monkeypatch.setattr(security, "_getaddrinfo", lambda h: [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("127.0.0.1", 0))])
    with pytest.raises(ValueError):
        pipeline.validate_source_url("https://sneaky.example/v")


def test_ipv6_name_resolving_to_loopback_rejected(monkeypatch):
    monkeypatch.setattr(security, "_getaddrinfo", lambda h: [(10, 1, 6, "", ("::1", 0, 0, 0))])
    with pytest.raises(ValueError):
        pipeline.validate_source_url("https://v6.example/v")


@pytest.mark.parametrize("url", [
    "https://www.twitch.tv/videos/123456789?t=1h2m3s", "https://youtu.be/abc", "http://93.184.216.34/video.mp4", "http://[2606:4700:4700::1111]/x",
])
def test_public_targets_accepted(url):
    assert pipeline.validate_source_url(url) == url


def test_unresolvable_host_is_not_blocked_by_the_guard(monkeypatch):
    def boom(h):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(security, "_getaddrinfo", boom)
    assert pipeline.validate_source_url("https://does-not-exist.invalid/v")  # the downloader will fail by itself


def test_allow_private_urls_switch(monkeypatch):
    _real_dns(monkeypatch)
    with pytest.raises(ValueError):
        pipeline.validate_source_url("http://192.168.1.10/v")
    monkeypatch.setattr(pipeline, "ALLOW_PRIVATE_URLS", True)
    assert pipeline.validate_source_url("http://192.168.1.10/v") == "http://192.168.1.10/v"


def test_syntax_checks_still_come_first():
    for bad in ["--exec=id", "file:///etc/passwd", "http://a.b/;rm"]:
        with pytest.raises(ValueError):
            pipeline.validate_source_url(bad)


def test_queue_api_rejects_private_url(client, monkeypatch):
    _real_dns(monkeypatch)
    r = client.post("/api/queue", json={"urls": ["http://169.254.169.254/latest/"]})
    assert r.status_code == 400
    r = client.post("/api/queue", json={"urls": ["http://169.254.169.254/latest/"]}, headers={"X-Lang": "fr"})
    assert "refusée" in r.json()["detail"]
