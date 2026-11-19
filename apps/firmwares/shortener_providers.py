"""Third-party URL shortener adapters over plain HTTPS APIs.

All HTTP goes through :class:`apps.seo.fetcher.SafeFetcher` (public https hosts only,
pinned DNS answer, bounded size and time, no cookies). The adapter shortens a URL on
*this* site (the download page or the first-party ``/d/<code>/`` link); the raw file
host URL is never sent to a provider.

Adapters:

* ``isgd`` and ``tinyurl``: no API key; fixed endpoints.
* ``query_api``: monetized shorteners (ouo.io, shrinkme and similar) that expose
  ``?api=KEY&url=``; the staff-entered endpoint carries ``{key}`` and ``{url}``.
* ``json_api``: generic GET or POST API with an optional auth header, a JSON body
  template and a dotted path to the short link in the answer.

Secrets: API keys can sit inside the request URL, and ``SafeFetcher`` messages may
quote the URL, so a failure is reduced to its stable ``code`` and the original message
is dropped (``raise ... from None``). Nothing here logs a URL, header or body.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

from apps.seo.fetcher import FetchError, FetchPolicy, FetchResult, SafeFetcher

from .shortener_constants import MAX_URL_LENGTH, HttpMethod, ProviderKind

PLACEHOLDER = re.compile(r"\{[^{}]*\}")
ALLOWED_PLACEHOLDERS = frozenset({"{url}", "{key}"})
KNOWN_KEYS = (
    "shortenedUrl",
    "shorturl",
    "short_url",
    "shortUrl",
    "short_link",
    "result_url",
    "link",
    "url",
)
NESTED_KEYS = ("data", "result", "response")
ANSWER_TYPES = ("text/plain", "text/html", "application/json")


class ProviderError(Exception):
    """A provider call failed. ``code`` is stable and safe to store or show."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Fetcher(Protocol):
    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        follow_redirects: bool = True,
    ) -> FetchResult: ...


@dataclass(frozen=True)
class ProviderConfig:
    """Everything an adapter needs; the key is never part of ``repr``."""

    kind: str
    endpoint: str = ""
    method: str = HttpMethod.GET
    api_key: str = field(default="", repr=False)
    auth_header: str = ""
    auth_prefix: str = ""
    body_template: str = ""
    response_path: str = ""
    timeout: int = 4


@dataclass(frozen=True)
class Call:
    method: str
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    body: bytes | None = field(default=None, repr=False)


def default_fetcher(timeout: int) -> SafeFetcher:
    """The SSRF-safe fetcher with the provider timeout and no redirect following."""
    base = FetchPolicy.from_settings()
    policy = replace(
        base,
        total_timeout=float(timeout),
        connect_timeout=min(base.connect_timeout, float(timeout)),
        max_redirects=0,
        max_bytes=65_536,
        allowed_content_types=ANSWER_TYPES,
        user_agent="MobofirmShortener/1.0",
    )
    return SafeFetcher(policy=policy)


# ---------------------------------------------------------------- templates ----


def fill_url(template: str, *, url: str, key: str) -> str:
    """Substitute the percent-encoded link and key into an endpoint template."""
    return template.replace("{key}", quote(key, safe="")).replace(
        "{url}", quote(url, safe="")
    )


def _fill_json(node: Any, *, url: str, key: str) -> Any:
    if isinstance(node, str):
        return node.replace("{key}", key).replace("{url}", url)
    if isinstance(node, list):
        return [_fill_json(item, url=url, key=key) for item in node]
    if isinstance(node, dict):
        return {
            str(name): _fill_json(value, url=url, key=key)
            for name, value in node.items()
        }
    return node


def endpoint_errors(endpoint: str, *, kind: str) -> list[str]:
    """Problems with a staff-entered endpoint template (empty list = fine)."""
    problems: list[str] = []
    if not endpoint:
        return ["Enter the provider endpoint."]
    if len(endpoint) > MAX_URL_LENGTH:
        problems.append("The endpoint is too long.")
    parts = urlsplit(endpoint.replace("{key}", "k").replace("{url}", "u"))
    if parts.scheme != "https" or not parts.hostname:
        problems.append("The endpoint must be an https URL.")
    if parts.username or parts.password:
        problems.append("Do not put credentials in the endpoint; use {key}.")
    unknown = set(PLACEHOLDER.findall(endpoint)) - ALLOWED_PLACEHOLDERS
    if unknown:
        problems.append("Only {url} and {key} are allowed as placeholders.")
    if kind == ProviderKind.QUERY_API and "{url}" not in endpoint:
        problems.append("The endpoint must contain {url}.")
    return problems


def body_template_errors(template: str) -> list[str]:
    if not template.strip():
        return ["Enter the JSON body for POST requests."]
    try:
        parsed = json.loads(template)
    except ValueError:
        return ["The body template is not valid JSON."]
    if not isinstance(parsed, dict):
        return ["The body template must be a JSON object."]
    if "{url}" not in template:
        return ["The body template must contain {url}."]
    return []


# ----------------------------------------------------------------- parsing ----


def _walk(data: Any, path: str) -> Any:
    node = data
    for step in path.split("."):
        if isinstance(node, dict) and step in node:
            node = node[step]
        elif isinstance(node, list) and step.isdigit() and int(step) < len(node):
            node = node[int(step)]
        else:
            raise ProviderError("no_short_url")
    return node


def _detect(data: Any) -> Any:
    if isinstance(data, dict):
        for name in KNOWN_KEYS:
            if isinstance(data.get(name), str):
                return data[name]
        for name in NESTED_KEYS:
            if isinstance(data.get(name), dict):
                return _detect(data[name])
    raise ProviderError("no_short_url")


def validate_short_url(value: object) -> str:
    """The provider's link when it is a plain https URL, else ``ProviderError``."""
    if not isinstance(value, str):
        raise ProviderError("no_short_url")
    text = value.strip()
    if not text or len(text) > MAX_URL_LENGTH or re.search(r"\s", text):
        raise ProviderError("bad_short_url")
    try:
        parts = urlsplit(text)
    except ValueError:
        raise ProviderError("bad_short_url") from None
    if parts.scheme != "https" or not parts.hostname:
        raise ProviderError("bad_short_url")
    if parts.username or parts.password:
        raise ProviderError("bad_short_url")
    return text


def extract_short_url(text: str, path: str = "") -> str:
    """Read the short link from a plain-text or JSON answer."""
    body = text.strip()
    if not body:
        raise ProviderError("empty_response")
    if body[0] in "{[":
        try:
            data = json.loads(body)
        except ValueError:
            raise ProviderError("bad_json") from None
        return validate_short_url(_walk(data, path) if path else _detect(data))
    return validate_short_url(body.splitlines()[0])


# ---------------------------------------------------------------- adapters ----


class Adapter:
    """One way of asking a provider for a short link."""

    kind = ""
    label = ""
    needs_key = False
    monetized = False

    def call(self, config: ProviderConfig, target: str) -> Call:
        raise NotImplementedError

    def parse(self, config: ProviderConfig, result: FetchResult) -> str:
        return extract_short_url(result.text, config.response_path)


class IsGdAdapter(Adapter):
    kind = ProviderKind.ISGD
    label = "is.gd"

    def call(self, config: ProviderConfig, target: str) -> Call:
        return Call(
            "GET",
            f"https://is.gd/create.php?format=simple&url={quote(target, safe='')}",
        )


class TinyUrlAdapter(Adapter):
    kind = ProviderKind.TINYURL
    label = "TinyURL"

    def call(self, config: ProviderConfig, target: str) -> Call:
        return Call(
            "GET",
            f"https://tinyurl.com/api-create.php?url={quote(target, safe='')}",
        )


class QueryApiAdapter(Adapter):
    """``?api=KEY&url=`` style shorteners (ouo.io, shrinkme and similar)."""

    kind = ProviderKind.QUERY_API
    label = "Monetized shortener"
    needs_key = True
    monetized = True

    def call(self, config: ProviderConfig, target: str) -> Call:
        url = fill_url(config.endpoint, url=target, key=config.api_key)
        return Call("GET", url, {"Accept": "application/json, text/plain;q=0.9"})


class JsonApiAdapter(Adapter):
    kind = ProviderKind.JSON_API
    label = "Generic JSON API"
    needs_key = False
    monetized = True

    def call(self, config: ProviderConfig, target: str) -> Call:
        url = fill_url(config.endpoint, url=target, key=config.api_key)
        headers = {"Accept": "application/json, text/plain;q=0.9"}
        if config.auth_header and config.api_key:
            headers[config.auth_header] = f"{config.auth_prefix}{config.api_key}"
        if config.method != HttpMethod.POST:
            return Call("GET", url, headers)
        try:
            template = json.loads(config.body_template)
        except ValueError:
            raise ProviderError("bad_body_template") from None
        payload = _fill_json(template, url=target, key=config.api_key)
        headers["Content-Type"] = "application/json"
        return Call("POST", url, headers, json.dumps(payload).encode("utf-8"))


ADAPTERS: dict[str, Adapter] = {
    adapter.kind: adapter
    for adapter in (
        IsGdAdapter(),
        TinyUrlAdapter(),
        QueryApiAdapter(),
        JsonApiAdapter(),
    )
}


def shorten(config: ProviderConfig, target: str, fetcher: Fetcher | None = None) -> str:
    """Ask the provider to shorten ``target``; raise :class:`ProviderError` on failure."""
    adapter = ADAPTERS.get(config.kind)
    if adapter is None:
        raise ProviderError("unknown_provider")
    call = adapter.call(config, target)
    client = fetcher or default_fetcher(config.timeout)
    try:
        result = client.fetch(
            call.url,
            method=call.method,
            headers=call.headers,
            body=call.body,
            follow_redirects=False,
        )
    except FetchError as error:
        # The message may quote the request URL (and so an API key): keep the code only.
        raise ProviderError(error.code) from None
    if result.status == 429:
        raise ProviderError("rate_limited")
    if not 200 <= result.status < 300:
        raise ProviderError(f"http_{result.status}")
    if result.body_skipped:
        raise ProviderError("unexpected_content_type")
    return adapter.parse(config, result)
