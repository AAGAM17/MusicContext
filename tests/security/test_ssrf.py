"""SSRF defences, with zero real network traffic.

`socket.getaddrinfo` and the module's two pinned connection classes are the only seams
needed to drive every branch, so nothing here can ever reach the internet.
"""

from __future__ import annotations

import socket

import pytest

from musiccontext.errors import RemoteDisabledError, UnsafeURLError
from musiccontext.security import network
from musiccontext.security.network import is_url, resolve_public, validate_url

PUBLIC_IP = "93.184.216.34"
BLOCKED = ["127.0.0.1", "10.0.0.5", "172.16.3.9", "192.168.1.1", "169.254.169.254", "::1", "::ffff:10.0.0.5"]


def _fake_dns(monkeypatch, mapping: dict[str, str] | str):
    """Point getaddrinfo at fixed answers. A str means 'every host resolves here'."""

    def gai(host, port, *args, **kwargs):
        ip = mapping if isinstance(mapping, str) else mapping[host]
        if ":" in ip:
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", gai)


class _Resp:
    """Minimal stand-in for http.client.HTTPResponse."""

    def __init__(self, status: int, headers: dict[str, str], body: bytes = b"", repeat: int = 1):
        self.status = status
        self._headers = {k.lower(): v for k, v in headers.items()}
        self._body = body
        self._left = repeat

    def getheader(self, name, default=None):
        return self._headers.get(name.lower(), default)

    def read(self, _n=-1):
        if self._left <= 0:
            return b""
        self._left -= 1
        return self._body


def _stub_connections(monkeypatch, responses: list[_Resp]):
    queue = list(responses)

    class Conn:
        def __init__(self, host, port, ip, timeout):
            self.host, self.ip = host, ip

        def request(self, method, path, headers=None):
            pass

        def getresponse(self):
            return queue.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(network, "_PinnedTLS", Conn)
    monkeypatch.setattr(network, "_Pinned", Conn)


@pytest.fixture
def remote(settings):
    settings.allow_remote = True
    return settings


# --- validate_url -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://example.com/clip.mp4", "plain http"),
        ("https://user:pass@example.com/clip.mp4", "embedded credentials"),
        ("https://:token@example.com/clip.mp4", "embedded credentials"),
        ("file:///etc/passwd", "non-http scheme"),
        ("gopher://example.com/1", "non-http scheme"),
        ("https:///clip.mp4", "no hostname"),
    ],
)
def test_validate_url_rejects_unsafe_urls(settings, url, why):
    with pytest.raises(UnsafeURLError):
        validate_url(url, settings)


def test_validate_url_accepts_https_and_keeps_the_query(settings):
    scheme, host, port, path = validate_url("https://cdn.example.com:8443/a/clip.mp4?v=2", settings)
    assert (scheme, host, port, path) == ("https", "cdn.example.com", 8443, "/a/clip.mp4?v=2")


def test_plain_http_is_allowed_only_when_explicitly_relaxed(settings):
    settings.allow_insecure_http = True
    assert validate_url("http://example.com/clip.mp4", settings)[0] == "http"


def test_is_url_only_matches_http_schemes():
    assert is_url("https://x.test/a") and is_url("HTTP://x.test/a")
    assert not is_url("/local/clip.mp4") and not is_url("file:///tmp/clip.mp4")


# --- resolve_public -----------------------------------------------------------------


@pytest.mark.parametrize("ip", BLOCKED)
def test_resolve_public_blocks_non_global_addresses(monkeypatch, ip):
    _fake_dns(monkeypatch, ip)
    with pytest.raises(UnsafeURLError) as ei:
        resolve_public("evil.example", 443, allow_private=False)
    assert "SSRF" in ei.value.message or "non-public" in ei.value.message


def test_resolve_public_allows_a_global_address(monkeypatch):
    _fake_dns(monkeypatch, PUBLIC_IP)
    assert resolve_public("cdn.example.com", 443, allow_private=False) == PUBLIC_IP


def test_resolve_public_reports_dns_failure_as_unsafe(monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(UnsafeURLError):
        resolve_public("nx.example", 443, allow_private=False)


# --- download -----------------------------------------------------------------------


def test_download_refuses_when_remote_is_disabled(settings):
    assert settings.allow_remote is False
    with pytest.raises(RemoteDisabledError) as ei:
        with network.download("https://cdn.example.com/clip.mp4", settings):
            pass
    assert "MUSICCONTEXT_ALLOW_REMOTE" in (ei.value.hint or "")


def test_redirect_to_an_internal_address_is_revalidated_and_blocked(monkeypatch, remote):
    """The classic bypass: a public first hop that 302s to the cloud metadata service."""
    _fake_dns(monkeypatch, {"cdn.example.com": PUBLIC_IP, "metadata.internal": "169.254.169.254"})
    _stub_connections(monkeypatch, [
        _Resp(302, {"Location": "https://metadata.internal/latest/meta-data/iam/"}),
        _Resp(200, {"Content-Type": "video/mp4"}, b"should never be reached"),
    ])
    with pytest.raises(UnsafeURLError) as ei:
        with network.download("https://cdn.example.com/clip.mp4", remote):
            pytest.fail("the redirect target was fetched instead of being blocked")
    assert "non-public" in ei.value.message


def test_redirect_chain_is_bounded(monkeypatch, remote):
    _fake_dns(monkeypatch, PUBLIC_IP)
    hops = [_Resp(302, {"Location": f"https://cdn.example.com/hop{i}"}) for i in range(8)]
    _stub_connections(monkeypatch, hops)
    with pytest.raises(UnsafeURLError) as ei:
        with network.download("https://cdn.example.com/clip.mp4", remote):
            pass
    assert "redirect" in ei.value.message.lower()


@pytest.mark.parametrize("ctype", ["text/html", "text/html; charset=utf-8", "application/json", ""])
def test_content_type_allowlist_rejects_non_media(monkeypatch, remote, ctype):
    _fake_dns(monkeypatch, PUBLIC_IP)
    _stub_connections(monkeypatch, [_Resp(200, {"Content-Type": ctype}, b"<html>phish</html>")])
    with pytest.raises(UnsafeURLError) as ei:
        with network.download("https://cdn.example.com/clip.mp4", remote):
            pass
    assert "content type" in ei.value.message.lower()


def test_declared_content_length_over_the_cap_is_refused(monkeypatch, remote):
    _fake_dns(monkeypatch, PUBLIC_IP)
    remote.max_download_bytes = 4096
    _stub_connections(monkeypatch, [_Resp(200, {"Content-Type": "video/mp4", "Content-Length": "999999"})])
    with pytest.raises(UnsafeURLError) as ei:
        with network.download("https://cdn.example.com/clip.mp4", remote):
            pass
    assert "larger than" in ei.value.message


def test_a_lying_server_is_cut_off_at_the_byte_cap(monkeypatch, remote):
    """No Content-Length, endless body: the streaming counter has to stop it."""
    _fake_dns(monkeypatch, PUBLIC_IP)
    remote.max_download_bytes = 4096
    _stub_connections(monkeypatch, [_Resp(200, {"Content-Type": "video/mp4"}, b"\0" * 65536, repeat=99)])
    with pytest.raises(UnsafeURLError) as ei:
        with network.download("https://cdn.example.com/clip.mp4", remote):
            pass
    assert "exceeded" in ei.value.message


def test_a_well_behaved_media_response_downloads_and_is_cleaned_up(monkeypatch, remote):
    _fake_dns(monkeypatch, PUBLIC_IP)
    _stub_connections(monkeypatch, [_Resp(200, {"Content-Type": "video/mp4"}, b"MOOV" * 16)])
    with network.download("https://cdn.example.com/clip.mp4", remote) as path:
        assert path.read_bytes() == b"MOOV" * 16
        assert path.name == "clip.mp4"
    assert not path.exists()  # the temp dir is removed on exit
    assert not path.parent.exists()
