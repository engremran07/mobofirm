"""SafeFetcher: the only outbound HTTP path allowed in ``apps.seo``.

Every request is validated before any byte leaves the process:

* only ``http``/``https`` URLs without embedded credentials and on allowed ports;
* the hostname is resolved once, every resolved address must be globally routable
  (loopback, private, link-local, CGNAT, metadata, multicast and reserved ranges are
  refused unless ``SEO_FETCH_ALLOW_PRIVATE_NETWORKS`` is enabled in local settings);
* the connection is pinned to the validated address, so a second DNS answer
  (DNS rebinding) cannot redirect the socket;
* redirects are followed manually and each hop repeats the full validation;
* response size, total time and concurrency are bounded; cookies are never sent and
  an ``Authorization`` header only goes to an explicitly allow-listed first host.
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urljoin, urlsplit

from django.conf import settings

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_TEXT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/xml",
    "application/xml",
    "text/plain",
    "application/json",
    "application/ld+json",
)


class FetchError(Exception):
    """Base class; ``code`` is a stable machine-readable reason."""

    code = "fetch_error"


class BlockedDestination(FetchError):
    code = "blocked_destination"


class ResponseTooLarge(FetchError):
    code = "response_too_large"


class FetchTimeout(FetchError):
    code = "timeout"


class TooManyRedirects(FetchError):
    code = "too_many_redirects"


class Busy(FetchError):
    code = "concurrency_limit"


def default_fetcher() -> SafeFetcher:
    """The fetcher every SEO feature uses; patched in tests, never bypassed."""
    return SafeFetcher()


@dataclass(frozen=True)
class FetchPolicy:
    allow_private_networks: bool = False
    allowed_ports: frozenset[int] = frozenset({80, 443})
    max_redirects: int = 5
    max_bytes: int = 2_000_000
    connect_timeout: float = 5.0
    total_timeout: float = 15.0
    max_concurrent: int = 4
    max_per_host: int = 2
    allowed_content_types: tuple[str, ...] = _TEXT_TYPES
    user_agent: str = "MobofirmSEOBot/1.0 (+site audit)"

    @classmethod
    def from_settings(cls) -> FetchPolicy:
        return cls(
            allow_private_networks=bool(
                getattr(settings, "SEO_FETCH_ALLOW_PRIVATE_NETWORKS", False)
            ),
            allowed_ports=frozenset(
                getattr(settings, "SEO_FETCH_ALLOWED_PORTS", (80, 443))
            ),
            max_bytes=int(getattr(settings, "SEO_FETCH_MAX_BYTES", 2_000_000)),
            total_timeout=float(getattr(settings, "SEO_FETCH_TOTAL_TIMEOUT", 15)),
            max_concurrent=int(getattr(settings, "SEO_FETCH_MAX_CONCURRENT", 4)),
        )


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    headers: Mapping[str, str]
    body: bytes
    redirects: tuple[tuple[int, str, str], ...] = ()
    elapsed_ms: int = 0
    body_skipped: bool = False

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def text(self) -> str:
        charset = "utf-8"
        for part in self.headers.get("content-type", "").split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip("\"'")
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


Resolver = Callable[[str, int], list[str]]
ConnectionFactory = Callable[
    [str, str, str, int, float], http.client.HTTPConnection
]  # scheme, hostname, pinned ip, port, timeout


def _default_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise FetchError(f"DNS resolution failed for {host}: {error}") from error
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float) -> None:
        self._tls = ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=self._tls)
        self._pinned_ip = ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        # SNI and certificate verification use the hostname, not the pinned IP.
        self.sock = self._tls.wrap_socket(raw, server_hostname=self.host)


def _default_connection(
    scheme: str, hostname: str, ip: str, port: int, timeout: float
) -> http.client.HTTPConnection:
    if scheme == "https":
        return _PinnedHTTPSConnection(hostname, ip, port, timeout)
    return _PinnedHTTPConnection(hostname, ip, port, timeout)


def is_blocked_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address that is not a publicly routable unicast address."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return is_blocked_address(address.ipv4_mapped)
        if address.sixtofour is not None:
            return is_blocked_address(address.sixtofour)
        if address.teredo is not None:
            return True
        if address in _NAT64:
            return is_blocked_address(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    return not address.is_global or address.is_multicast


_global_slots: dict[int, threading.BoundedSemaphore] = {}
_slots_lock = threading.Lock()
_host_slots: dict[tuple[int, str], threading.BoundedSemaphore] = {}


@dataclass
class SafeFetcher:
    policy: FetchPolicy = field(default_factory=FetchPolicy.from_settings)
    resolver: Resolver = _default_resolver
    connection_factory: ConnectionFactory = _default_connection
    clock: Callable[[], float] = time.monotonic

    def _semaphore(self, limit: int, host: str | None) -> threading.BoundedSemaphore:
        with _slots_lock:
            if host is None:
                return _global_slots.setdefault(
                    limit, threading.BoundedSemaphore(limit)
                )
            return _host_slots.setdefault(
                (limit, host), threading.BoundedSemaphore(limit)
            )

    @contextlib.contextmanager
    def _slot(self, host: str) -> Iterator[None]:
        slots = [
            self._semaphore(self.policy.max_concurrent, None),
            self._semaphore(self.policy.max_per_host, host),
        ]
        acquired: list[threading.BoundedSemaphore] = []
        try:
            for slot in slots:
                if not slot.acquire(timeout=self.policy.total_timeout):
                    raise Busy("Too many concurrent outbound requests.")
                acquired.append(slot)
            yield
        finally:
            for slot in reversed(acquired):
                slot.release()

    def validate_url(
        self, url: str, allowed_hosts: frozenset[str] | None = None
    ) -> SplitResult:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as error:
            raise BlockedDestination(f"Malformed URL: {error}") from error
        if parts.scheme not in {"http", "https"}:
            raise BlockedDestination(f"Scheme {parts.scheme!r} is not allowed.")
        if not parts.hostname:
            raise BlockedDestination("URL has no host.")
        if parts.username is not None or parts.password is not None:
            raise BlockedDestination("URLs with embedded credentials are refused.")
        host = parts.hostname.lower().rstrip(".")
        if allowed_hosts is not None and host not in allowed_hosts:
            raise BlockedDestination(f"Host {host!r} is not on the allow-list.")
        effective_port = port or (443 if parts.scheme == "https" else 80)
        if (
            not self.policy.allow_private_networks
            and effective_port not in self.policy.allowed_ports
        ):
            raise BlockedDestination(f"Port {effective_port} is not allowed.")
        return parts

    def pin_address(self, hostname: str, port: int) -> str:
        """Resolve once and return the validated address the socket must use."""
        try:
            literal = ipaddress.ip_address(hostname.strip("[]"))
        except ValueError:
            addresses = self.resolver(hostname, port)
        else:
            addresses = [str(literal)]
        if not addresses:
            raise FetchError(f"No addresses for {hostname}.")
        if not self.policy.allow_private_networks:
            for address in addresses:
                try:
                    parsed = ipaddress.ip_address(address)
                except ValueError as error:
                    raise BlockedDestination(
                        f"Unparseable address {address!r}."
                    ) from error
                if is_blocked_address(parsed):
                    raise BlockedDestination(
                        f"{hostname} resolves to non-public address {address}."
                    )
        return addresses[0]

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        follow_redirects: bool = True,
        allowed_hosts: frozenset[str] | None = None,
    ) -> FetchResult:
        method = method.upper()
        if method not in {"GET", "HEAD", "POST"}:
            raise FetchError(f"Method {method} is not supported.")
        started = self.clock()
        deadline = started + self.policy.total_timeout
        current = url
        first_host = ""
        chain: list[tuple[int, str, str]] = []
        for _hop in range(self.policy.max_redirects + 1):
            parts = self.validate_url(current, allowed_hosts)
            hostname = (parts.hostname or "").lower().rstrip(".")
            first_host = first_host or hostname
            port = parts.port or (443 if parts.scheme == "https" else 80)
            ip = self.pin_address(hostname, port)
            # Credentials are sent only to an explicitly allow-listed first host.
            send_credentials = allowed_hosts is not None and hostname == first_host
            with self._slot(hostname):
                status, response_headers, payload, skipped = self._request_once(
                    parts,
                    hostname=hostname,
                    ip=ip,
                    port=port,
                    method=method,
                    headers=headers,
                    body=body,
                    deadline=deadline,
                    send_credentials=send_credentials,
                )
            location = response_headers.get("location", "")
            if (
                follow_redirects
                and method != "POST"
                and status in REDIRECT_STATUSES
                and location
            ):
                target = urljoin(current, location)
                chain.append((status, current, target))
                current = target
                continue
            return FetchResult(
                url=url,
                final_url=current,
                status=status,
                headers=response_headers,
                body=payload,
                redirects=tuple(chain),
                elapsed_ms=int((self.clock() - started) * 1000),
                body_skipped=skipped,
            )
        raise TooManyRedirects(
            f"More than {self.policy.max_redirects} redirects from {url}."
        )

    def _request_once(
        self,
        parts: SplitResult,
        *,
        hostname: str,
        ip: str,
        port: int,
        method: str,
        headers: Mapping[str, str] | None,
        body: bytes | None,
        deadline: float,
        send_credentials: bool,
    ) -> tuple[int, dict[str, str], bytes, bool]:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise FetchTimeout("Total fetch time exceeded.")
        connection = self.connection_factory(
            parts.scheme,
            hostname,
            ip,
            port,
            min(self.policy.connect_timeout, remaining),
        )
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        request_headers = {
            "User-Agent": self.policy.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        blocked_headers = {"cookie", "proxy-authorization"}
        if not send_credentials:
            blocked_headers.add("authorization")
        for name, value in (headers or {}).items():
            if name.lower() not in blocked_headers:
                request_headers[name] = value
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            response_headers = {
                key.lower(): value for key, value in response.getheaders()
            }
            declared = response_headers.get("content-length", "")
            if declared.isdigit() and int(declared) > self.policy.max_bytes:
                raise ResponseTooLarge(f"Declared size {declared} exceeds the limit.")
            content_type = response_headers.get("content-type", "")
            media_type = content_type.split(";")[0].strip().lower()
            if (
                method == "HEAD"
                or response.status in REDIRECT_STATUSES
                or (
                    media_type
                    and not media_type.startswith(self.policy.allowed_content_types)
                )
            ):
                return (
                    response.status,
                    response_headers,
                    b"",
                    method != "HEAD" and response.status not in REDIRECT_STATUSES,
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                if self.clock() > deadline:
                    raise FetchTimeout("Total fetch time exceeded.")
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.policy.max_bytes:
                    raise ResponseTooLarge("Response exceeds the size limit.")
                chunks.append(chunk)
            return response.status, response_headers, b"".join(chunks), False
        except TimeoutError as error:
            raise FetchTimeout(f"Timed out fetching {parts.geturl()}.") from error
        except (OSError, http.client.HTTPException) as error:
            raise FetchError(f"Request to {hostname} failed: {error}") from error
        finally:
            connection.close()
