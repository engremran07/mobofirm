"""Platform adapters: one class per platform, found through a registry.

Adding a platform = writing one ``Adapter`` subclass in ``apps.distribution.platforms``
decorated with ``@register`` (plus one line in ``Channel.Kind``; a test keeps the two in
step). An adapter declares what it needs (credential/option fields rendered as the channel
form, media requirement, message limit, hashtag and link rules, rate limits) and knows how
to ``publish``, ``check`` (test the connection) and ``preview`` (dry run, no network).

All HTTP goes through ``apps.seo.fetcher.SafeFetcher`` via ``Http``: public https hosts
only, never following redirects, credentials only to an allow-listed host, secrets scrubbed
from every message that is stored or shown.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlencode, urlsplit

from apps.distribution.constants import MEDIA_IMAGE, MEDIA_TEXT, MEDIA_VIDEO
from apps.seo.fetcher import (
    BlockedDestination,
    FetchError,
    FetchResult,
    SafeFetcher,
)

if TYPE_CHECKING:
    from apps.distribution.models import Channel, ShareJob

RETRYABLE_STATUSES = frozenset({408, 425, 429})
_LINK = re.compile(r"https?://\S+")
DEFAULT_WINDOWS = "09:00-11:00,17:00-20:00"


class ChannelConfigError(ValueError):
    """The channel's saved configuration cannot produce a valid request."""


@dataclass(frozen=True, slots=True)
class OutboundRequest:
    url: str
    headers: dict[str, str]
    body: bytes
    allowed_hosts: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class Outcome:
    ok: bool
    retryable: bool
    code: str
    detail: str = ""
    external_id: str = ""
    retry_after: int = 0


@dataclass(frozen=True, slots=True)
class CredentialField:
    """One value the staff member enters for a platform."""

    name: str
    label: str
    help: str = ""
    # secret -> encrypted at rest; option -> channel.options; target/endpoint_url -> model columns
    store: str = "secret"
    required: bool = True
    choices: tuple[tuple[str, str], ...] = ()
    placeholder: str = ""

    @property
    def is_secret(self) -> bool:
        return self.store == "secret"


@dataclass(slots=True)
class MediaRefs:
    """Public URLs / storage names of the share assets available for a job."""

    image_url: str = ""
    vertical_url: str = ""
    video_url: str = ""
    image_name: str = ""
    video_name: str = ""
    reader: Callable[[str], bytes] | None = None

    def video_bytes(self) -> bytes:
        if not (self.video_name and self.reader):
            raise ChannelConfigError("No generated video is available.")
        return self.reader(self.video_name)


@dataclass(frozen=True, slots=True)
class Preview:
    """What a dry run shows: no network, no secrets."""

    text: str
    length: int
    limit: int
    requires: str
    media_problem: str
    steps: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def sendable(self) -> bool:
        return not self.media_problem and not self.warnings


class StepError(Exception):
    """A step of a multi-request publish failed with a final outcome."""

    def __init__(self, outcome: Outcome) -> None:
        super().__init__(outcome.detail)
        self.outcome = outcome


def snippet(body: bytes) -> str:
    """A short human reason out of a platform error body."""
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return ""
    candidates: list[Any] = []
    if isinstance(data, dict):
        error = data.get("error")
        candidates = [
            error.get("message") if isinstance(error, dict) else error,
            data.get("detail"),
            data.get("message"),
            data.get("error_description"),
            data.get("description"),
        ]
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            candidates.append(
                first.get("message") if isinstance(first, dict) else str(first)
            )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate[:160]
    return ""


def classify_status(
    status: int, headers: Mapping[str, str], body: bytes = b""
) -> Outcome:
    """2xx = ok; 408/425/429/5xx = retry; any other status is terminal."""
    if 200 <= status < 300:
        return Outcome(True, False, str(status))
    retry_after = headers.get("retry-after", "")
    delay = int(retry_after) if retry_after.isdigit() else 0
    if status in RETRYABLE_STATUSES or status >= 500:
        return Outcome(False, True, str(status), f"HTTP {status}", retry_after=delay)
    reason = snippet(body)
    detail = f"HTTP {status}: the platform refused the post"
    return Outcome(
        False, False, str(status), f"{detail} ({reason})" if reason else detail
    )


class Http:
    """SafeFetcher wrapper that maps failures to ``StepError`` and scrubs secrets."""

    def __init__(self, fetcher: SafeFetcher, secrets: Iterable[str] = ()) -> None:
        self.fetcher = fetcher
        self.secrets = [s for s in secrets if s and len(s) >= 4]

    def scrub(self, message: str) -> str:
        for secret in self.secrets:
            message = message.replace(secret, "***")
        return message

    def long(self, seconds: float = 300) -> Http:
        """A copy for large uploads (the stock 15 s budget is too short)."""
        fetcher = self.fetcher
        if type(fetcher) is SafeFetcher:
            fetcher = replace(
                fetcher, policy=replace(fetcher.policy, total_timeout=seconds)
            )
        return Http(fetcher, self.secrets)

    def call(
        self,
        url: str,
        *,
        method: str = "POST",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        hosts: frozenset[str] | None = None,
    ) -> FetchResult:
        try:
            return self.fetcher.fetch(
                url,
                method=method,
                headers=headers,
                body=body,
                follow_redirects=False,
                allowed_hosts=hosts,
            )
        except BlockedDestination as error:
            raise StepError(
                Outcome(False, False, "blocked", self.scrub(str(error)))
            ) from error
        except FetchError as error:
            raise StepError(
                Outcome(False, True, error.code, self.scrub(str(error)))
            ) from error

    def json(
        self,
        url: str,
        *,
        method: str = "POST",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        hosts: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """A request that must be 2xx; returns the decoded JSON object."""
        result = self.call(url, method=method, headers=headers, body=body, hosts=hosts)
        if not 200 <= result.status < 300:
            outcome = classify_status(result.status, result.headers, result.body)
            raise StepError(replace(outcome, detail=self.scrub(outcome.detail)))
        try:
            data = json.loads(result.body or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


def json_body(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


def form_body(payload: Mapping[str, Any]) -> bytes:
    return urlencode({k: v for k, v in payload.items() if v is not None}).encode()


JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
FORM_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass(slots=True)
class SendContext:
    """Everything an adapter needs for one publish or check."""

    channel: Channel
    job: ShareJob
    values: dict[str, str]
    http: Http
    media: MediaRefs = field(default_factory=MediaRefs)
    updated: dict[str, str] = field(default_factory=dict)

    def value(self, name: str) -> str:
        return (self.values.get(name) or "").strip()

    @property
    def state(self) -> dict[str, Any]:
        return self.job.remote_state


class Adapter:
    """Base class: declares requirements; subclasses implement the calls."""

    kind: ClassVar[str] = ""
    label: ClassVar[str] = ""
    media: ClassVar[str] = MEDIA_TEXT
    image_optional: ClassVar[bool] = False
    message_limit: ClassVar[int] = 2000
    # Length one link counts for (X counts every link as 23); 0 = its real length.
    url_length: ClassVar[int] = 0
    hashtag_limit: ClassVar[int] = 0
    emoji_ok: ClassVar[bool] = True
    fields: ClassVar[tuple[CredentialField, ...]] = ()
    how_to: ClassVar[str] = ""
    docs_url: ClassVar[str] = ""
    # Platform courtesy limits: minimum gap between posts, hard daily ceiling (0 = none).
    min_interval: ClassVar[int] = 0
    daily_max: ClassVar[int] = 0
    default_windows: ClassVar[str] = DEFAULT_WINDOWS
    legacy_secret_field: ClassVar[str] = ""
    endpoints: ClassVar[tuple[str, ...]] = ()
    supports_check: ClassVar[bool] = True

    # ------------------------------------------------------------- config ----

    @classmethod
    def secret_fields(cls) -> tuple[CredentialField, ...]:
        return tuple(f for f in cls.fields if f.is_secret)

    @classmethod
    def problem(cls, values: Mapping[str, str]) -> str:
        missing = [
            f.label for f in cls.fields if f.required and not values.get(f.name, "")
        ]
        if missing:
            return f"{cls.label} needs: {', '.join(missing)}."
        return cls.extra_problem(values)

    @classmethod
    def extra_problem(cls, values: Mapping[str, str]) -> str:
        del values  # hook: platforms with cross-field rules override this
        return ""

    # -------------------------------------------------------------- media ----

    def required_media(self, values: Mapping[str, str]) -> str:
        del values  # hook: platforms whose media need depends on settings override this
        return self.media

    def media_problem(
        self, media: MediaRefs, values: Mapping[str, str] | None = None
    ) -> str:
        """Why the available assets cannot satisfy this platform ('' when fine)."""
        needs = self.required_media(values or {})
        if needs == MEDIA_IMAGE and not media.image_url:
            return "Needs an image: the share card could not be generated."
        if needs == MEDIA_VIDEO and not media.video_url:
            return "Needs a video: no vertical video has been generated."
        return ""

    # ------------------------------------------------------------ actions ----

    def publish(self, ctx: SendContext) -> Outcome:
        raise NotImplementedError

    def check(self, ctx: SendContext) -> Outcome:
        del ctx  # hook: platforms with a read-only credential check override this
        return Outcome(
            False,
            False,
            "unsupported",
            f"{self.label} cannot be verified without posting; use “Send test post”.",
        )

    def preview(
        self, text: str, values: Mapping[str, str], media: MediaRefs
    ) -> Preview:
        warnings: list[str] = []
        problem = self.problem(values)
        if problem:
            warnings.append(problem)
        length = visible_length(text, self.url_length)
        if length > self.message_limit:
            warnings.append(
                f"Text is {length} characters; limit is {self.message_limit}."
            )
        return Preview(
            text=text,
            length=length,
            limit=self.message_limit,
            requires=self.required_media(values),
            media_problem=self.media_problem(media, values),
            steps=self.endpoints,
            warnings=tuple(warnings),
        )


def visible_length(text: str, url_length: int = 0) -> int:
    """Characters a platform counts: links may count as a fixed length."""
    if not url_length:
        return len(text)
    return len(_LINK.sub("x" * url_length, text))


_REGISTRY: dict[str, Adapter] = {}


def register(cls: type[Adapter]) -> type[Adapter]:
    _REGISTRY[cls.kind] = cls()
    return cls


def adapters() -> dict[str, Adapter]:
    from apps.distribution import platforms

    platforms.load()
    return _REGISTRY


def get_adapter(kind: str) -> Adapter | None:
    return adapters().get(kind)


# ---------------------------------------------------------- credentials ----


def load_values(channel: Channel, secret_plain: str) -> dict[str, str]:
    """Every configured value for ``channel`` by field name (secrets included).

    A stored secret is either a JSON object of field values (multi-secret platforms) or,
    for rows created before this release, one plain string that belongs to the platform's
    ``legacy_secret_field``.
    """
    adapter = get_adapter(channel.kind)
    values: dict[str, str] = {}
    if adapter is None:
        return values
    options = channel.options if isinstance(channel.options, dict) else {}
    for spec in adapter.fields:
        if spec.store == "option":
            values[spec.name] = str(options.get(spec.name, "") or "")
        elif spec.store == "target":
            values[spec.name] = channel.target
        elif spec.store == "endpoint_url":
            values[spec.name] = channel.endpoint_url
    if secret_plain.startswith("{"):
        try:
            stored = json.loads(secret_plain)
        except ValueError:
            stored = {}
        if isinstance(stored, dict):
            values.update({str(k): str(v) for k, v in stored.items()})
            return values
    if secret_plain and adapter.legacy_secret_field:
        values[adapter.legacy_secret_field] = secret_plain
    return values


def pack_secrets(adapter: Adapter, values: Mapping[str, str]) -> str:
    """The plaintext to encrypt: a bare string for one-secret platforms (the legacy
    layout) or a JSON object when there are several."""
    secrets = {
        spec.name: values[spec.name]
        for spec in adapter.secret_fields()
        if values.get(spec.name)
    }
    if not secrets:
        return ""
    if len(adapter.secret_fields()) == 1 and adapter.legacy_secret_field in secrets:
        return secrets[adapter.legacy_secret_field]
    return json.dumps(secrets, separators=(",", ":"))


def stored_secret_names(channel: Channel, secret_plain: str) -> set[str]:
    return {name for name, value in load_values(channel, secret_plain).items() if value}


def configuration_problem(channel: Channel, secret_plain: str) -> str:
    """Why the channel cannot send yet, judged from its values ('' = complete)."""
    adapter = get_adapter(channel.kind)
    if adapter is None:
        return f"Unknown platform {channel.kind!r}."
    return adapter.problem(load_values(channel, secret_plain))


# --------------------------------------------- legacy single-request API ----


def build_request(channel: Channel, job: ShareJob, secret: str) -> OutboundRequest:
    """The HTTPS request for ``job`` on a single-request platform (webhook, Telegram,
    Discord, X, Mastodon, Slack); ``secret`` is the decrypted credential."""
    adapter = get_adapter(channel.kind)
    if not isinstance(adapter, SimpleAdapter):
        raise ChannelConfigError(f"Unknown channel kind {channel.kind!r}.")
    values = load_values(channel, secret)
    return adapter.request(channel, job, values)


class SimpleAdapter(Adapter):
    """A platform that takes one POST per share."""

    def request(
        self,
        channel: Channel,
        job: ShareJob,
        values: Mapping[str, str],
        media: MediaRefs | None = None,
    ) -> OutboundRequest:
        raise NotImplementedError

    def parse_id(self, body: bytes) -> str:
        del body  # hook: platforms that return a post id override this
        return ""

    def publish(self, ctx: SendContext) -> Outcome:
        try:
            request = self.request(ctx.channel, ctx.job, ctx.values, ctx.media)
        except ChannelConfigError as error:
            return Outcome(False, False, "config", str(error))
        try:
            result = ctx.http.call(
                request.url,
                headers=request.headers,
                body=request.body,
                hosts=request.allowed_hosts,
            )
        except StepError as error:
            return error.outcome
        outcome = classify_status(result.status, result.headers, result.body)
        if not outcome.ok:
            return replace(outcome, detail=ctx.http.scrub(outcome.detail))
        return replace(outcome, external_id=self.parse_id(result.body))


def external_id(kind: str, body: bytes) -> str:
    """The platform's id for the posted message, when the response carries one."""
    adapter = get_adapter(kind)
    return adapter.parse_id(body) if isinstance(adapter, SimpleAdapter) else ""


def webhook_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def https_host(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return ""
    return parts.hostname.lower()


__all__ = [
    "MEDIA_IMAGE",
    "MEDIA_TEXT",
    "MEDIA_VIDEO",
    "Adapter",
    "ChannelConfigError",
    "CredentialField",
    "Http",
    "MediaRefs",
    "OutboundRequest",
    "Outcome",
    "Preview",
    "SendContext",
    "SimpleAdapter",
    "StepError",
    "adapters",
    "build_request",
    "classify_status",
    "configuration_problem",
    "external_id",
    "get_adapter",
    "load_values",
    "pack_secrets",
    "register",
]
