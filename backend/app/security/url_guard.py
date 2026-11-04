"""SSRF protection for URLs the freelancer controls.

PactPay's browser fetches whatever URL a freelancer submits. Without a guard, a submission
like http://169.254.169.254/latest/meta-data/ (cloud metadata) or http://10.0.0.5:5432
would make *our* server probe *our* network. Defense in depth:

1. `check_url` validates the submitted URL before anything runs (API returns 422).
2. `URLGuard.allowed` is called for EVERY request the browser makes (pages, scripts, images,
   fetches), so a public page can't pull in internal resources either.
3. After navigation the final URL is re-checked (catches redirects to internal hosts).

Residual risk (documented in docs/security.md): DNS rebinding between our lookup and
Chromium's. Full isolation needs an egress firewall on the verification worker.
"""

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], Awaitable[list[str]]]

SAFE_PORTS = {None, 80, 443}
BROWSER_INTERNAL_SCHEMES = {"data", "blob", "about"}
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
CACHE_SECONDS = 60


class UnsafeURLError(ValueError):
    """The URL could make PactPay reach something it must not."""


async def system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # strip IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class URLGuard:
    def __init__(
        self,
        *,
        allow_private: bool = False,
        resolver: Resolver = system_resolver,
    ) -> None:
        self.allow_private = allow_private
        self._resolver = resolver
        self._cache: dict[str, tuple[float, bool, str]] = {}

    async def check_url(self, url: str) -> None:
        """Raise UnsafeURLError unless `url` is a public http(s) URL."""
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise UnsafeURLError(f"Only http(s) URLs are allowed, got {parts.scheme or 'none'}")
        if parts.username or parts.password:
            raise UnsafeURLError("URLs with embedded credentials are not allowed")
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            raise UnsafeURLError("URL has no host")
        try:
            port = parts.port
        except ValueError as exc:
            raise UnsafeURLError("URL has an invalid port") from exc
        if self.allow_private:
            return
        if port not in SAFE_PORTS:
            raise UnsafeURLError(f"Port {port} is not allowed; use 80 or 443")
        if host == "localhost" or host.endswith(BLOCKED_HOST_SUFFIXES):
            raise UnsafeURLError(f"{host} is an internal host name")
        ok, reason = await self._host_is_public(host)
        if not ok:
            raise UnsafeURLError(reason)

    async def allowed(self, url: str) -> bool:
        """Fast yes/no for the browser's request interceptor."""
        if urlsplit(url).scheme in BROWSER_INTERNAL_SCHEMES:
            return True
        try:
            await self.check_url(url)
        except UnsafeURLError:
            return False
        return True

    async def _host_is_public(self, host: str) -> tuple[bool, str]:
        cached = self._cache.get(host)
        if cached and cached[0] > time.monotonic():
            return cached[1], cached[2]
        try:
            addresses = await self._resolver(host)
        except (OSError, UnicodeError):
            result = (False, f"Could not resolve {host}")
        else:
            private = [a for a in addresses if not is_public_ip(a)]
            if not addresses:
                result = (False, f"Could not resolve {host}")
            elif private:
                # ALL addresses must be public: a mixed answer is a rebinding red flag.
                result = (False, f"{host} resolves to a private or reserved address")
            else:
                result = (True, "")
        self._cache[host] = (time.monotonic() + CACHE_SECONDS, *result)
        return result
