"""SSRF-safe remote download. Disabled unless explicitly enabled in settings.

Defences: https-only (unless explicitly relaxed), DNS resolved once and the
connection pinned to the validated IP (no rebinding), non-global addresses
blocked, redirects re-validated hop by hop, content-type allowlist, byte cap,
overall timeout, temp-file cleanup.
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import os
import socket
import ssl
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from ..errors import RemoteDisabledError, UnsafeURLError

ALLOWED_TYPES = ("video/", "audio/", "application/octet-stream", "application/mp4", "application/ogg")
MAX_REDIRECTS = 3


def is_url(s: str) -> bool:
    return s.lower().startswith(("http://", "https://"))


def resolve_public(host: str, port: int, allow_private: bool) -> str:
    """Resolve host and return one validated IP. Rejects any non-global answer."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise UnsafeURLError(f"Cannot resolve host '{host}'.") from None
    chosen = None
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not allow_private and not ip.is_global:
            raise UnsafeURLError(
                f"Host '{host}' resolves to a non-public address; blocked to prevent SSRF.",
                "Private, loopback, link-local and metadata addresses are never fetched.",
            )
        chosen = chosen or str(ip)
    if not chosen:
        raise UnsafeURLError(f"Host '{host}' has no usable address.")
    return chosen


class _Pinned(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedTLS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def validate_url(url: str, settings) -> tuple[str, str, int, str]:
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed.")
    if parts.scheme == "http" and not settings.allow_insecure_http:
        raise UnsafeURLError("Only https:// URLs are allowed.", "Plain http is refused by default.")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeURLError("Only http(s) URLs with a hostname are supported.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return parts.scheme, parts.hostname, port, path


@contextlib.contextmanager
def download(url: str, settings, *, timeout: float | None = None) -> Iterator[Path]:
    """Download `url` into a temp file; the file is removed when the context exits."""
    if not settings.allow_remote:
        raise RemoteDisabledError()
    timeout = timeout or settings.remote_timeout
    deadline = time.monotonic() + timeout * 3
    tmpdir = tempfile.mkdtemp(prefix="musiccontext-dl-")
    try:
        cur = url
        for _hop in range(MAX_REDIRECTS + 1):
            scheme, host, port, path = validate_url(cur, settings)
            ip = resolve_public(host, port, settings.allow_private_network)
            conn = (_PinnedTLS if scheme == "https" else _Pinned)(host, port, ip, timeout)
            try:
                conn.request("GET", path, headers={"Host": host if port in (80, 443) else f"{host}:{port}", "User-Agent": "musiccontext", "Accept": "video/*,audio/*"})
                resp = conn.getresponse()
                if resp.status in (301, 302, 303, 307, 308):
                    loc = resp.getheader("Location")
                    if not loc:
                        raise UnsafeURLError("Redirect without Location header.")
                    cur = urljoin(cur, loc)
                    continue
                if resp.status != 200:
                    raise UnsafeURLError(f"Remote server returned HTTP {resp.status}.")
                ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
                if not ctype.startswith(ALLOWED_TYPES):
                    raise UnsafeURLError(f"Unexpected content type '{ctype or 'none'}'; expected audio or video.")
                clen = resp.getheader("Content-Length")
                if clen and clen.isdigit() and int(clen) > settings.max_download_bytes:
                    raise UnsafeURLError(f"Remote file is larger than the {settings.max_download_bytes} byte limit.")
                name = Path(urlsplit(cur).path).name or "download"
                target = Path(tmpdir) / ("".join(c for c in name if c.isalnum() or c in "._-")[:80] or "download")
                total = 0
                with open(target, "wb") as fh:
                    while chunk := resp.read(1 << 16):
                        total += len(chunk)
                        if total > settings.max_download_bytes:
                            raise UnsafeURLError(f"Download exceeded the {settings.max_download_bytes} byte limit.")
                        if time.monotonic() > deadline:
                            raise UnsafeURLError("Download timed out.")
                        fh.write(chunk)
                yield target
                return
            except (OSError, http.client.HTTPException) as e:
                if isinstance(e, UnsafeURLError):
                    raise
                raise UnsafeURLError(f"Download failed: {type(e).__name__}.") from None
            finally:
                conn.close()
        raise UnsafeURLError(f"Too many redirects (>{MAX_REDIRECTS}).")
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def env_flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")
