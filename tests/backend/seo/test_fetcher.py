from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from apps.seo.fetcher import (
    BlockedDestination,
    Busy,
    FetchError,
    FetchPolicy,
    FetchTimeout,
    ResponseTooLarge,
    SafeFetcher,
    TooManyRedirects,
    is_blocked_address,
)

PUBLIC_IP = "93.184.216.34"


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b"", chunk=65536):
        self.status = status
        self._headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
        self._body = body
        self._chunk = chunk

    def getheaders(self):
        return list(self._headers.items())

    def read(self, amount):
        data, self._body = self._body[:amount], self._body[amount:]
        return data


class FakeConnection:
    def __init__(self, log, script, host, ip):
        self.log, self.script, self.host, self.ip = log, script, host, ip

    def request(self, method, path, body=None, headers=None):
        self.log.append(
            {
                "host": self.host,
                "ip": self.ip,
                "method": method,
                "path": path,
                "headers": dict(headers or {}),
            }
        )

    def getresponse(self):
        return self.script[self.host].pop(0)

    def close(self):
        pass


class Network:
    """Fake DNS and sockets so tests never touch a real network."""

    def __init__(self, dns, script=None):
        self.dns = dns
        self.script = script or {}
        self.log = []
        self.lookups = []

    def resolver(self, host, port):
        self.lookups.append(host)
        answer = self.dns[host]
        if isinstance(answer, list) and answer and isinstance(answer[0], list):
            return answer[min(len(self.lookups) - 1, len(answer) - 1)]
        return answer

    def connect(self, scheme, host, ip, port, timeout):
        return FakeConnection(self.log, self.script, host, ip)

    def fetcher(self, **policy):
        return SafeFetcher(
            policy=FetchPolicy(**policy),
            resolver=self.resolver,
            connection_factory=self.connect,
        )


def test_scheme_credentials_and_port_are_validated():
    fetcher = Network({}).fetcher()
    for url in (
        "file:///etc/passwd",
        "ftp://example.org/x",
        "gopher://example.org/",
        "http://user:pw@example.org/",
        "http://example.org:8080/",
        "http:///nohost",
        "javascript:alert(1)",
    ):
        with pytest.raises(BlockedDestination):
            fetcher.fetch(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.4.4",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "100.100.100.200",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "fe80::1",
        "fd00:ec2::254",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "64:ff9b::7f00:1",
        "2002:7f00:1::1",
    ],
)
def test_non_public_literal_addresses_are_refused_before_any_connection(address):
    network = Network({})
    host = f"[{address}]" if ":" in address else address

    with pytest.raises(BlockedDestination):
        network.fetcher().fetch(f"http://{host}/")

    assert network.log == []


def test_public_address_helper_accepts_global_unicast_only():
    import ipaddress

    assert not is_blocked_address(ipaddress.ip_address(PUBLIC_IP))
    assert not is_blocked_address(ipaddress.ip_address("2606:2800:220:1::1"))
    assert is_blocked_address(ipaddress.ip_address("10.1.1.1"))


def test_hostname_resolving_to_private_or_mixed_addresses_is_blocked():
    network = Network(
        {"internal.example": ["10.0.0.7"], "mixed.example": [PUBLIC_IP, "127.0.0.1"]}
    )

    for host in ("internal.example", "mixed.example"):
        with pytest.raises(BlockedDestination):
            network.fetcher().fetch(f"http://{host}/")

    assert network.log == []


def test_dns_rebinding_cannot_change_the_pinned_address():
    # The first answer is public; any second lookup would be private.
    network = Network(
        {"rebind.example": [[PUBLIC_IP], ["127.0.0.1"]]},
        {"rebind.example": [FakeResponse(body=b"ok")]},
    )

    result = network.fetcher().fetch("http://rebind.example/")

    assert result.status == 200
    assert network.lookups == ["rebind.example"]
    assert [entry["ip"] for entry in network.log] == [PUBLIC_IP]


def test_every_redirect_hop_is_revalidated():
    network = Network(
        {"a.example": [PUBLIC_IP], "internal.example": ["10.0.0.9"]},
        {
            "a.example": [
                FakeResponse(302, {"Location": "http://internal.example/admin"})
            ]
        },
    )

    with pytest.raises(BlockedDestination):
        network.fetcher().fetch("http://a.example/")

    assert [entry["host"] for entry in network.log] == ["a.example"]


@pytest.mark.parametrize(
    "location",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:80/",
        "file:///etc/passwd",
        "gopher://a.example/",
        "http://[::1]/",
    ],
)
def test_redirects_to_private_or_unsafe_targets_are_refused(location):
    network = Network(
        {"a.example": [PUBLIC_IP]},
        {"a.example": [FakeResponse(301, {"Location": location})]},
    )

    with pytest.raises(BlockedDestination):
        network.fetcher().fetch("http://a.example/")


def test_redirect_loops_are_bounded():
    network = Network(
        {"a.example": [PUBLIC_IP]},
        {"a.example": [FakeResponse(302, {"Location": "/again"}) for _ in range(10)]},
    )

    with pytest.raises(TooManyRedirects):
        network.fetcher(max_redirects=3).fetch("http://a.example/")


def test_redirects_are_followed_and_recorded():
    network = Network(
        {"a.example": [PUBLIC_IP], "b.example": [PUBLIC_IP]},
        {
            "a.example": [FakeResponse(301, {"Location": "http://b.example/new"})],
            "b.example": [FakeResponse(body=b"<html>done</html>")],
        },
    )

    result = network.fetcher().fetch("http://a.example/old")

    assert result.final_url == "http://b.example/new"
    assert result.redirects == ((301, "http://a.example/old", "http://b.example/new"),)
    assert result.text == "<html>done</html>"


def test_cookies_and_authorization_are_not_forwarded_by_default():
    network = Network(
        {"a.example": [PUBLIC_IP]}, {"a.example": [FakeResponse(), FakeResponse()]}
    )
    fetcher = network.fetcher()

    fetcher.fetch(
        "http://a.example/",
        headers={"Cookie": "sessionid=1", "Authorization": "Bearer x", "X-Test": "1"},
    )
    fetcher.fetch(
        "http://a.example/",
        headers={"Authorization": "Bearer x"},
        allowed_hosts=frozenset({"a.example"}),
    )

    first, second = (entry["headers"] for entry in network.log)
    assert "Cookie" not in first and "Authorization" not in first
    assert first["X-Test"] == "1"
    assert second["Authorization"] == "Bearer x"


def test_host_allow_list_is_enforced_on_every_hop():
    network = Network(
        {"a.example": [PUBLIC_IP], "b.example": [PUBLIC_IP]},
        {"a.example": [FakeResponse(302, {"Location": "http://b.example/"})]},
    )

    with pytest.raises(BlockedDestination):
        network.fetcher().fetch(
            "http://a.example/", allowed_hosts=frozenset({"a.example"})
        )


def test_response_size_limits_apply_to_declared_and_streamed_bodies():
    network = Network(
        {"a.example": [PUBLIC_IP]},
        {
            "a.example": [
                FakeResponse(headers={"Content-Length": "5000"}, body=b"x"),
                FakeResponse(body=b"y" * 5000),
            ]
        },
    )
    fetcher = network.fetcher(max_bytes=1000)

    with pytest.raises(ResponseTooLarge):
        fetcher.fetch("http://a.example/")
    with pytest.raises(ResponseTooLarge):
        fetcher.fetch("http://a.example/")


def test_binary_content_types_are_not_downloaded():
    network = Network(
        {"a.example": [PUBLIC_IP]},
        {
            "a.example": [
                FakeResponse(headers={"Content-Type": "application/zip"}, body=b"PK")
            ]
        },
    )

    result = network.fetcher().fetch("http://a.example/file.zip")

    assert result.body == b"" and result.body_skipped


def test_total_time_budget_is_enforced():
    ticks = iter(range(0, 1000, 20))
    network = Network(
        {"a.example": [PUBLIC_IP]}, {"a.example": [FakeResponse(body=b"z" * 200000)]}
    )
    fetcher = SafeFetcher(
        policy=FetchPolicy(total_timeout=30),
        resolver=network.resolver,
        connection_factory=network.connect,
        clock=lambda: float(next(ticks)),
    )

    with pytest.raises(FetchTimeout):
        fetcher.fetch("http://a.example/")


def test_concurrency_cap_rejects_instead_of_queueing_forever():
    network = Network({"a.example": [PUBLIC_IP]}, {"a.example": [FakeResponse()]})
    fetcher = network.fetcher(max_concurrent=1, total_timeout=0.05)
    slot = fetcher._semaphore(1, None)
    slot.acquire()
    try:
        with pytest.raises(Busy):
            fetcher.fetch("http://a.example/")
    finally:
        slot.release()


def test_dns_failures_surface_as_fetch_errors():
    def failing(host, port):
        raise FetchError("DNS resolution failed")

    with pytest.raises(FetchError):
        SafeFetcher(resolver=failing).fetch("http://nowhere.example/")


@contextmanager
def local_server(handler_class) -> Iterator[int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
        elif self.path == "/big":
            body = b"a" * 50_000
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"<html><title>live</title></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def test_real_socket_fetch_is_blocked_unless_private_networks_are_enabled():
    with local_server(_Handler) as port:
        with pytest.raises(BlockedDestination):
            SafeFetcher(policy=FetchPolicy()).fetch(f"http://127.0.0.1:{port}/page")

        local = SafeFetcher(
            policy=FetchPolicy(allow_private_networks=True, max_bytes=10_000)
        )
        page = local.fetch(f"http://127.0.0.1:{port}/redirect")
        assert page.status == 200 and "live" in page.text
        assert page.redirects[0][0] == 302
        with pytest.raises(ResponseTooLarge):
            local.fetch(f"http://127.0.0.1:{port}/big")
