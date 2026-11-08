"""All platform adapters. Each class is self-contained; see ``channels.Adapter``.

Facts that matter when operating them (checked against the platforms' public API docs;
API versions are constants so a platform bump is a one-line change):

* Meta (Facebook/Instagram/WhatsApp/Threads) tokens are long-lived page/system tokens.
* YouTube has no community-post API: only Shorts-style video upload is supported, and
  projects that are not audited by Google upload as private until audited.
* TikTok's Content Posting API posts privately until the app is audited; videos are pulled
  from a verified URL prefix, so the site's media domain must be verified in TikTok.
* WhatsApp Channels (newsletters) have no posting API; the Cloud API sends approved
  template messages to recipients who opted in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from apps.distribution.channels import (
    FORM_HEADERS,
    JSON_HEADERS,
    Adapter,
    ChannelConfigError,
    CredentialField,
    MediaRefs,
    OutboundRequest,
    Outcome,
    SendContext,
    SimpleAdapter,
    StepError,
    bearer,
    classify_status,
    form_body,
    https_host,
    json_body,
    register,
    webhook_signature,
)
from apps.distribution.constants import (
    KIND_BLUESKY,
    KIND_DISCORD,
    KIND_FACEBOOK,
    KIND_INSTAGRAM,
    KIND_LINKEDIN,
    KIND_MASTODON,
    KIND_PINTEREST,
    KIND_REDDIT,
    KIND_SLACK,
    KIND_TELEGRAM,
    KIND_THREADS,
    KIND_TIKTOK,
    KIND_WEBHOOK,
    KIND_WHATSAPP,
    KIND_X,
    KIND_YOUTUBE,
    MEDIA_IMAGE,
    MEDIA_VIDEO,
)
from apps.distribution.models import ShareJob

GRAPH_VERSION = "v21.0"
GRAPH_HOST = "graph.facebook.com"
THREADS_HOST = "graph.threads.net"
THREADS_VERSION = "v1.0"
LINKEDIN_VERSION = "202506"
REDDIT_UA = "web:mobofirm-distribution:1.0 (staff-run site account)"
MAX_UPLOAD_BYTES = 60 * 1024 * 1024

DISCORD_HOSTS = frozenset(
    {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
)
TELEGRAM_HOST = "api.telegram.org"
X_HOST = "api.x.com"
_LOADED = {"done": False}


def load() -> None:
    """Importing this module registers every adapter; nothing else to do."""
    _LOADED["done"] = True


def _host(hostname: str) -> frozenset[str]:
    return frozenset({hostname})


def first_line(text: str, limit: int) -> str:
    line = text.strip().split("\n", 1)[0].strip()
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _id(data: Mapping[str, Any], key: str = "id") -> str:
    value = data.get(key, "")
    return str(value) if value else ""


def refresh_access_token(
    ctx: SendContext,
    url: str,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    basic: bool,
    extra: Mapping[str, str] | None = None,
    user_agent: str = "",
) -> str:
    """Exchange a refresh token for a short-lived access token (stateless per send).

    A rotated refresh token is handed back through ``ctx.updated`` and persisted by the
    pipeline, so the next send keeps working.
    """
    payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    headers = dict(FORM_HEADERS)
    if basic:
        pair = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {pair}"
    else:
        payload.update({"client_id": client_id, "client_secret": client_secret})
    if user_agent:
        headers["User-Agent"] = user_agent
    payload.update(extra or {})
    data = ctx.http.json(
        url,
        headers=headers,
        body=form_body(payload),
        hosts=_host(urlsplit(url).hostname or ""),
    )
    token = _id(data, "access_token")
    if not token:
        raise StepError(
            Outcome(False, False, "auth", "The platform returned no access token.")
        )
    ctx.http.secrets.append(token)
    rotated = _id(data, "refresh_token")
    if rotated and rotated != refresh_token:
        ctx.updated["refresh_token"] = rotated
    return token


# --------------------------------------------------------------- webhook ----


@register
class WebhookAdapter(SimpleAdapter):
    kind = KIND_WEBHOOK
    label = "Generic webhook"
    message_limit = 4000
    fields = (
        CredentialField(
            "endpoint_url",
            "Endpoint URL",
            "Public https address that receives a signed JSON POST.",
            store="endpoint_url",
            placeholder="https://hooks.example.com/mobofirm",
        ),
        CredentialField(
            "signing_secret",
            "Signing secret",
            "Optional: the body is signed with HMAC-SHA256 in X-Distribution-Signature.",
            required=False,
        ),
    )
    legacy_secret_field = "signing_secret"
    how_to = "Use it for Zapier, Make, n8n or your own service: it receives title, url, text and an idempotency key."
    endpoints = ("POST <endpoint URL> (JSON, signed)",)

    @classmethod
    def extra_problem(cls, values: Mapping[str, str]) -> str:
        if not https_host(values.get("endpoint_url", "")):
            return "A webhook needs a public https URL."
        return ""

    def request(
        self,
        channel: Any,
        job: ShareJob,
        values: Mapping[str, str],
        media: MediaRefs | None = None,
    ) -> OutboundRequest:
        url = values.get("endpoint_url", "")
        if not https_host(url):
            raise ChannelConfigError("A webhook needs a public https URL.")
        body = json_body(
            {
                "event": f"distribution.{job.content_kind}",
                "kind": job.content_kind,
                "title": job.title,
                "url": job.url,
                "text": job.text,
                "idempotency_key": job.idempotency_key,
            }
        )
        headers = {**JSON_HEADERS, "X-Idempotency-Key": job.idempotency_key}
        secret = values.get("signing_secret", "")
        if secret:
            headers["X-Distribution-Signature"] = (
                f"sha256={webhook_signature(secret, body)}"
            )
        return OutboundRequest(url=url, headers=headers, body=body, allowed_hosts=None)

    def check(self, ctx: SendContext) -> Outcome:
        body = json_body({"event": "distribution.ping", "kind": "ping"})
        headers = dict(JSON_HEADERS)
        if ctx.value("signing_secret"):
            headers["X-Distribution-Signature"] = (
                f"sha256={webhook_signature(ctx.value('signing_secret'), body)}"
            )
        try:
            result = ctx.http.call(
                ctx.value("endpoint_url"), headers=headers, body=body
            )
        except StepError as error:
            return error.outcome
        return classify_status(result.status, result.headers, result.body)


# -------------------------------------------------------------- telegram ----


@register
class TelegramAdapter(SimpleAdapter):
    kind = KIND_TELEGRAM
    label = "Telegram channel or group"
    message_limit = 4096
    image_optional = True
    hashtag_limit = 3
    min_interval = 20
    fields = (
        CredentialField(
            "chat_id",
            "Chat id or @channel",
            "Add the bot as an administrator of the channel, then use @channelname or the numeric id.",
            store="target",
            placeholder="@mobofirm",
        ),
        CredentialField(
            "bot_token",
            "Bot token",
            "Create a bot with @BotFather and paste the token.",
            placeholder="123456:ABC…",
        ),
    )
    legacy_secret_field = "bot_token"
    how_to = "Talk to @BotFather → /newbot → copy the token; make the bot an admin of your channel."
    endpoints = ("POST api.telegram.org/bot…/sendMessage (or sendPhoto with an image)",)

    def request(
        self,
        channel: Any,
        job: ShareJob,
        values: Mapping[str, str],
        media: MediaRefs | None = None,
    ) -> OutboundRequest:
        token, chat = values.get("bot_token", ""), values.get("chat_id", "")
        if not (token and chat):
            raise ChannelConfigError("Telegram needs a chat id and a bot token.")
        if media and media.image_url and len(job.text) <= 1024:
            method, payload = (
                "sendPhoto",
                {"chat_id": chat, "photo": media.image_url, "caption": job.text},
            )
        else:
            method, payload = "sendMessage", {"chat_id": chat, "text": job.text}
        return OutboundRequest(
            url=f"https://{TELEGRAM_HOST}/bot{token}/{method}",
            headers=dict(JSON_HEADERS),
            body=json_body(payload),
            allowed_hosts=_host(TELEGRAM_HOST),
        )

    def parse_id(self, body: bytes) -> str:
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return ""
        result = data.get("result") if isinstance(data, dict) else None
        return str(result.get("message_id", "")) if isinstance(result, dict) else ""

    def check(self, ctx: SendContext) -> Outcome:
        try:
            data = ctx.http.json(
                f"https://{TELEGRAM_HOST}/bot{ctx.value('bot_token')}/getMe",
                method="GET",
                hosts=_host(TELEGRAM_HOST),
            )
        except StepError as error:
            return error.outcome
        name = (data.get("result") or {}).get("username", "")
        return Outcome(
            True, False, "200", f"Connected as @{name}" if name else "Connected"
        )


# --------------------------------------------------------------- discord ----


@register
class DiscordAdapter(SimpleAdapter):
    kind = KIND_DISCORD
    label = "Discord channel (webhook)"
    message_limit = 2000
    image_optional = True
    min_interval = 5
    fields = (
        CredentialField(
            "webhook_url",
            "Webhook URL",
            "Channel settings → Integrations → Webhooks → Copy Webhook URL.",
            placeholder="https://discord.com/api/webhooks/…",
        ),
    )
    legacy_secret_field = "webhook_url"
    how_to = "In Discord: channel settings → Integrations → Webhooks → New webhook → Copy URL."
    endpoints = ("POST discord.com/api/webhooks/… (mentions disabled)",)

    @classmethod
    def extra_problem(cls, values: Mapping[str, str]) -> str:
        return (
            ""
            if _discord_ok(values.get("webhook_url", ""))
            else ("Enter a https://discord.com/api/webhooks/… URL.")
        )

    def request(
        self,
        channel: Any,
        job: ShareJob,
        values: Mapping[str, str],
        media: MediaRefs | None = None,
    ) -> OutboundRequest:
        secret = values.get("webhook_url", "")
        if not _discord_ok(secret):
            raise ChannelConfigError("Enter a https://discord.com/api/webhooks/… URL.")
        # No mention parsing: shared text can never ping @everyone.
        payload: dict[str, Any] = {
            "content": job.text,
            "allowed_mentions": {"parse": []},
        }
        if media and media.image_url:
            payload["embeds"] = [{"image": {"url": media.image_url}}]
        return OutboundRequest(
            url=f"{secret.split('?', 1)[0]}?wait=true",
            headers=dict(JSON_HEADERS),
            body=json_body(payload),
            allowed_hosts=None,
        )

    def parse_id(self, body: bytes) -> str:
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return ""
        return str(data.get("id", "")) if isinstance(data, dict) else ""

    def check(self, ctx: SendContext) -> Outcome:
        url = ctx.value("webhook_url")
        if not _discord_ok(url):
            return Outcome(False, False, "config", "Enter a discord.com webhook URL.")
        try:
            ctx.http.json(url.split("?", 1)[0], method="GET")
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", "Webhook is valid")


def _discord_ok(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and (parts.hostname or "").lower() in DISCORD_HOSTS
        and parts.path.startswith("/api/webhooks/")
    )


# ----------------------------------------------------------------- slack ----


@register
class SlackAdapter(SimpleAdapter):
    kind = KIND_SLACK
    label = "Slack (incoming webhook)"
    message_limit = 3000
    min_interval = 2
    fields = (
        CredentialField(
            "webhook_url",
            "Incoming webhook URL",
            "Slack app → Incoming Webhooks → Add New Webhook to Workspace.",
            placeholder="https://hooks.slack.com/services/…",
        ),
    )
    legacy_secret_field = "webhook_url"
    how_to = "api.slack.com/apps → your app → Incoming Webhooks → Add New Webhook to Workspace."
    endpoints = ("POST hooks.slack.com/services/…",)
    supports_check = False

    @classmethod
    def extra_problem(cls, values: Mapping[str, str]) -> str:
        url = values.get("webhook_url", "")
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "hooks.slack.com":
            return "Enter a https://hooks.slack.com/services/… URL."
        return ""

    def request(
        self,
        channel: Any,
        job: ShareJob,
        values: Mapping[str, str],
        media: MediaRefs | None = None,
    ) -> OutboundRequest:
        problem = self.extra_problem(values)
        if problem:
            raise ChannelConfigError(problem)
        return OutboundRequest(
            url=values["webhook_url"],
            headers=dict(JSON_HEADERS),
            body=json_body({"text": job.text, "link_names": 0}),
            allowed_hosts=_host("hooks.slack.com"),
        )


# ------------------------------------------------------------------- x ----


@register
class XAdapter(Adapter):
    kind = KIND_X
    label = "X (Twitter)"
    message_limit = 280
    url_length = 23
    hashtag_limit = 2
    min_interval = 600
    daily_max = 50
    fields = (
        CredentialField(
            "access_token",
            "User access token",
            "OAuth 2.0 user-context token with tweet.write and offline.access.",
        ),
        CredentialField(
            "refresh_token",
            "Refresh token",
            "Optional but recommended: user tokens expire after two hours.",
            required=False,
        ),
        CredentialField(
            "client_id", "Client id", "Optional (needed to refresh).", required=False
        ),
        CredentialField(
            "client_secret",
            "Client secret",
            "Optional (confidential apps).",
            required=False,
        ),
    )
    legacy_secret_field = "access_token"
    how_to = "developer.x.com → your project → OAuth 2.0 user context (tweet.write, offline.access) → authorise the account once and paste the tokens."
    endpoints = ("POST api.x.com/2/tweets",)

    @classmethod
    def extra_problem(cls, values: Mapping[str, str]) -> str:
        if values.get("refresh_token") and not values.get("client_id"):
            return "A refresh token needs the client id too."
        return ""

    def _token(self, ctx: SendContext) -> str:
        if ctx.value("refresh_token") and ctx.value("client_id"):
            return refresh_access_token(
                ctx,
                f"https://{X_HOST}/2/oauth2/token",
                client_id=ctx.value("client_id"),
                client_secret=ctx.value("client_secret"),
                refresh_token=ctx.value("refresh_token"),
                basic=bool(ctx.value("client_secret")),
            )
        return ctx.value("access_token")

    def publish(self, ctx: SendContext) -> Outcome:
        try:
            token = self._token(ctx)
            if not token:
                raise StepError(
                    Outcome(False, False, "config", "Enter the X user access token.")
                )
            data = ctx.http.json(
                f"https://{X_HOST}/2/tweets",
                headers={**JSON_HEADERS, **bearer(token)},
                body=json_body({"text": ctx.job.text}),
                hosts=_host(X_HOST),
            )
        except StepError as error:
            return error.outcome
        inner = data.get("data")
        return Outcome(
            True,
            False,
            "200",
            external_id=_id(inner) if isinstance(inner, dict) else "",
        )

    def check(self, ctx: SendContext) -> Outcome:
        try:
            token = self._token(ctx)
            data = ctx.http.json(
                f"https://{X_HOST}/2/users/me",
                method="GET",
                headers=bearer(token),
                hosts=_host(X_HOST),
            )
        except StepError as error:
            return error.outcome
        inner = data.get("data")
        name = inner.get("username", "") if isinstance(inner, dict) else ""
        return Outcome(
            True, False, "200", f"Connected as @{name}" if name else "Connected"
        )


# ------------------------------------------------------------- mastodon ----


@register
class MastodonAdapter(Adapter):
    kind = KIND_MASTODON
    label = "Mastodon"
    message_limit = 500
    url_length = 23
    hashtag_limit = 4
    min_interval = 60
    fields = (
        CredentialField(
            "instance_url",
            "Server URL",
            "Your instance, for example https://mastodon.social.",
            store="option",
            placeholder="https://mastodon.social",
        ),
        CredentialField(
            "access_token",
            "Access token",
            "Preferences → Development → New application (scope write:statuses).",
        ),
    )
    legacy_secret_field = "access_token"
    how_to = "Preferences → Development → New application with the write:statuses scope → copy “Your access token”."
    endpoints = ("POST <server>/api/v1/statuses (Idempotency-Key)",)

    @classmethod
    def extra_problem(cls, values: Mapping[str, str]) -> str:
        return (
            ""
            if https_host(values.get("instance_url", ""))
            else "The server URL must be https."
        )

    def _base(self, ctx: SendContext) -> tuple[str, frozenset[str]]:
        host = https_host(ctx.value("instance_url"))
        if not host:
            raise StepError(
                Outcome(False, False, "config", "The server URL must be https.")
            )
        return f"https://{host}", _host(host)

    def publish(self, ctx: SendContext) -> Outcome:
        try:
            base, hosts = self._base(ctx)
            data = ctx.http.json(
                f"{base}/api/v1/statuses",
                headers={
                    **JSON_HEADERS,
                    **bearer(ctx.value("access_token")),
                    "Idempotency-Key": ctx.job.idempotency_key[:100],
                },
                body=json_body({"status": ctx.job.text, "visibility": "public"}),
                hosts=hosts,
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", external_id=_id(data))

    def check(self, ctx: SendContext) -> Outcome:
        try:
            base, hosts = self._base(ctx)
            data = ctx.http.json(
                f"{base}/api/v1/accounts/verify_credentials",
                method="GET",
                headers=bearer(ctx.value("access_token")),
                hosts=hosts,
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", f"Connected as @{data.get('acct', '')}")


# -------------------------------------------------------------- bluesky ----

_URL_RE = re.compile(rb"https?://[^\s]+")
_TAG_RE = re.compile(rb"(?<![\w#])#([A-Za-z0-9_]{1,64})")


def bluesky_facets(text: str) -> list[dict[str, Any]]:
    """Link and hashtag facets with UTF-8 byte offsets (what the AT protocol needs)."""
    raw = text.encode()
    facets: list[dict[str, Any]] = []
    for match in _URL_RE.finditer(raw):
        uri = match.group(0).rstrip(b".,;:!?)").decode()
        end = match.start() + len(uri.encode())
        facets.append(
            {
                "index": {"byteStart": match.start(), "byteEnd": end},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": uri}],
            }
        )
    for match in _TAG_RE.finditer(raw):
        facets.append(
            {
                "index": {"byteStart": match.start(), "byteEnd": match.end()},
                "features": [
                    {
                        "$type": "app.bsky.richtext.facet#tag",
                        "tag": match.group(1).decode(),
                    }
                ],
            }
        )
    return facets


@register
class BlueskyAdapter(Adapter):
    kind = KIND_BLUESKY
    label = "Bluesky"
    message_limit = 300
    hashtag_limit = 3
    min_interval = 60
    fields = (
        CredentialField(
            "handle",
            "Handle",
            "Your handle, for example mobofirm.bsky.social.",
            store="option",
            placeholder="mobofirm.bsky.social",
        ),
        CredentialField(
            "pds_url",
            "Server",
            "Leave empty for bsky.social; only change it for a self-hosted PDS.",
            store="option",
            required=False,
            placeholder="https://bsky.social",
        ),
        CredentialField(
            "app_password",
            "App password",
            "Settings → Privacy and security → App passwords (never your main password).",
        ),
    )
    legacy_secret_field = "app_password"
    how_to = "Bluesky → Settings → Privacy and security → App passwords → Add; paste it here with your handle."
    endpoints = (
        "POST <server>/xrpc/com.atproto.server.createSession",
        "POST <server>/xrpc/com.atproto.repo.createRecord (app.bsky.feed.post)",
    )

    def _server(self, ctx: SendContext) -> tuple[str, frozenset[str]]:
        host = https_host(ctx.value("pds_url") or "https://bsky.social")
        if not host:
            raise StepError(
                Outcome(False, False, "config", "The server URL must be https.")
            )
        return f"https://{host}", _host(host)

    def _session(self, ctx: SendContext) -> tuple[str, str, str, frozenset[str]]:
        base, hosts = self._server(ctx)
        data = ctx.http.json(
            f"{base}/xrpc/com.atproto.server.createSession",
            headers=dict(JSON_HEADERS),
            body=json_body(
                {
                    "identifier": ctx.value("handle"),
                    "password": ctx.value("app_password"),
                }
            ),
            hosts=hosts,
        )
        token, did = _id(data, "accessJwt"), _id(data, "did")
        if not (token and did):
            raise StepError(
                Outcome(False, False, "auth", "Bluesky did not return a session.")
            )
        ctx.http.secrets.append(token)
        return base, token, did, hosts

    def publish(self, ctx: SendContext) -> Outcome:
        try:
            base, token, did, hosts = self._session(ctx)
            record: dict[str, Any] = {
                "$type": "app.bsky.feed.post",
                "text": ctx.job.text,
                "createdAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "langs": ["en"],
            }
            facets = bluesky_facets(ctx.job.text)
            if facets:
                record["facets"] = facets
            if ctx.job.url:
                record["embed"] = {
                    "$type": "app.bsky.embed.external",
                    "external": {
                        "uri": ctx.job.url,
                        "title": ctx.job.title[:200],
                        "description": "",
                    },
                }
            data = ctx.http.json(
                f"{base}/xrpc/com.atproto.repo.createRecord",
                headers={**JSON_HEADERS, **bearer(token)},
                body=json_body(
                    {"repo": did, "collection": "app.bsky.feed.post", "record": record}
                ),
                hosts=hosts,
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", external_id=_id(data, "uri"))

    def check(self, ctx: SendContext) -> Outcome:
        try:
            _base, _token, did, _hosts = self._session(ctx)
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", f"Signed in as {did}")


# ------------------------------------------------------------- facebook ----


def _graph(path: str) -> str:
    return f"https://{GRAPH_HOST}/{GRAPH_VERSION}/{path}"


@register
class FacebookAdapter(Adapter):
    kind = KIND_FACEBOOK
    label = "Facebook Page"
    message_limit = 5000
    image_optional = True
    hashtag_limit = 2
    min_interval = 300
    daily_max = 25
    fields = (
        CredentialField(
            "page_id",
            "Page id",
            "Page → About → Page transparency → Page ID.",
            store="option",
        ),
        CredentialField(
            "access_token",
            "Page access token",
            "Long-lived Page token with pages_manage_posts and pages_read_engagement.",
        ),
    )
    legacy_secret_field = "access_token"
    how_to = "developers.facebook.com → your app → Graph API Explorer → generate a Page access token (pages_manage_posts), exchange it for a long-lived one."
    endpoints = (
        f"POST {GRAPH_HOST}/{GRAPH_VERSION}/<page>/feed (or /photos with a share card)",
    )

    def publish(self, ctx: SendContext) -> Outcome:
        page = ctx.value("page_id")
        headers = {**FORM_HEADERS, **bearer(ctx.value("access_token"))}
        payload: dict[str, str | None]
        if ctx.media.image_url:
            path = f"{page}/photos"
            payload = {"url": ctx.media.image_url, "caption": ctx.job.text}
        else:
            path = f"{page}/feed"
            payload = {"message": ctx.job.text, "link": ctx.job.url or None}
        try:
            data = ctx.http.json(
                _graph(path),
                headers=headers,
                body=form_body(payload),
                hosts=_host(GRAPH_HOST),
            )
        except StepError as error:
            return error.outcome
        return Outcome(
            True, False, "200", external_id=_id(data, "post_id") or _id(data)
        )

    def check(self, ctx: SendContext) -> Outcome:
        try:
            data = ctx.http.json(
                _graph(f"{ctx.value('page_id')}?fields=id,name"),
                method="GET",
                headers=bearer(ctx.value("access_token")),
                hosts=_host(GRAPH_HOST),
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", f"Page: {data.get('name', '')}")


# ------------------------------------------------------------ instagram ----


@register
class InstagramAdapter(Adapter):
    kind = KIND_INSTAGRAM
    label = "Instagram (Graph API)"
    media = MEDIA_IMAGE
    message_limit = 2200
    hashtag_limit = 8
    min_interval = 900
    daily_max = 25
    fields = (
        CredentialField(
            "ig_user_id",
            "Instagram business account id",
            "The IG user id linked to your Facebook Page (Graph API Explorer: /me/accounts → instagram_business_account).",
            store="option",
        ),
        CredentialField(
            "format",
            "Post format",
            "Feed image, or Reel when a generated vertical video exists.",
            store="option",
            required=False,
            choices=(("feed", "Feed image"), ("reel", "Reel (needs video)")),
        ),
        CredentialField(
            "access_token",
            "Access token",
            "Long-lived token with instagram_basic and instagram_content_publish.",
        ),
    )
    legacy_secret_field = "access_token"
    how_to = "Convert the account to Professional, link it to a Facebook Page, then create a Graph API token with instagram_content_publish."
    endpoints = (
        f"POST {GRAPH_HOST}/{GRAPH_VERSION}/<ig>/media (image_url or REELS video_url)",
        f"POST {GRAPH_HOST}/{GRAPH_VERSION}/<ig>/media_publish",
    )

    def _reel(self, ctx_values: Mapping[str, str]) -> bool:
        return ctx_values.get("format", "feed") == "reel"

    def required_media(self, values: Mapping[str, str]) -> str:
        return MEDIA_VIDEO if self._reel(values) else MEDIA_IMAGE

    def publish(self, ctx: SendContext) -> Outcome:
        ig = ctx.value("ig_user_id")
        headers = {**FORM_HEADERS, **bearer(ctx.value("access_token"))}
        hosts = _host(GRAPH_HOST)
        reel = self._reel(ctx.values)
        try:
            if reel and not ctx.media.video_url:
                return Outcome(
                    False, False, "media", "Reel format needs a generated video."
                )
            if "creation_id" not in ctx.state:
                payload: dict[str, Any] = {"caption": ctx.job.text}
                if reel:
                    payload.update(
                        {"media_type": "REELS", "video_url": ctx.media.video_url}
                    )
                else:
                    payload["image_url"] = ctx.media.image_url
                data = ctx.http.json(
                    _graph(f"{ig}/media"),
                    headers=headers,
                    body=form_body(payload),
                    hosts=hosts,
                )
                ctx.state["creation_id"] = _id(data)
            creation = ctx.state["creation_id"]
            if reel:
                status = ctx.http.json(
                    _graph(f"{creation}?fields=status_code"),
                    method="GET",
                    headers=bearer(ctx.value("access_token")),
                    hosts=hosts,
                ).get("status_code", "")
                if status == "IN_PROGRESS":
                    return Outcome(
                        False,
                        True,
                        "processing",
                        "Instagram is still processing the video",
                        retry_after=90,
                    )
                if status in {"ERROR", "EXPIRED"}:
                    ctx.state.pop("creation_id", None)
                    return Outcome(
                        False,
                        False,
                        "media",
                        f"Instagram rejected the video ({status}).",
                    )
            data = ctx.http.json(
                _graph(f"{ig}/media_publish"),
                headers=headers,
                body=form_body({"creation_id": creation}),
                hosts=hosts,
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", external_id=_id(data))

    def check(self, ctx: SendContext) -> Outcome:
        try:
            data = ctx.http.json(
                _graph(f"{ctx.value('ig_user_id')}?fields=id,username"),
                method="GET",
                headers=bearer(ctx.value("access_token")),
                hosts=_host(GRAPH_HOST),
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", f"Account: @{data.get('username', '')}")


# -------------------------------------------------------------- threads ----


def _threads(path: str) -> str:
    return f"https://{THREADS_HOST}/{THREADS_VERSION}/{path}"


@register
class ThreadsAdapter(Adapter):
    kind = KIND_THREADS
    label = "Threads"
    message_limit = 500
    hashtag_limit = 1
    min_interval = 600
    daily_max = 250
    fields = (
        CredentialField(
            "threads_user_id",
            "Threads user id",
            "Returned by GET /me with your token.",
            store="option",
        ),
        CredentialField(
            "access_token",
            "Access token",
            "Long-lived token with threads_basic and threads_content_publish.",
        ),
    )
    legacy_secret_field = "access_token"
    how_to = "developers.facebook.com → your app → Threads API use case → generate a user token with threads_content_publish."
    endpoints = (
        f"POST {THREADS_HOST}/{THREADS_VERSION}/<id>/threads (TEXT)",
        f"POST {THREADS_HOST}/{THREADS_VERSION}/<id>/threads_publish",
    )

    def publish(self, ctx: SendContext) -> Outcome:
        uid = ctx.value("threads_user_id")
        headers = {**FORM_HEADERS, **bearer(ctx.value("access_token"))}
        hosts = _host(THREADS_HOST)
        try:
            if "creation_id" not in ctx.state:
                data = ctx.http.json(
                    _threads(f"{uid}/threads"),
                    headers=headers,
                    body=form_body({"media_type": "TEXT", "text": ctx.job.text}),
                    hosts=hosts,
                )
                ctx.state["creation_id"] = _id(data)
            data = ctx.http.json(
                _threads(f"{uid}/threads_publish"),
                headers=headers,
                body=form_body({"creation_id": ctx.state["creation_id"]}),
                hosts=hosts,
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", external_id=_id(data))

    def check(self, ctx: SendContext) -> Outcome:
        try:
            data = ctx.http.json(
                _threads("me?fields=id,username"),
                method="GET",
                headers=bearer(ctx.value("access_token")),
                hosts=_host(THREADS_HOST),
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", f"Account: @{data.get('username', '')}")


# ------------------------------------------------------------- linkedin ----


@register
class LinkedInAdapter(Adapter):
    kind = KIND_LINKEDIN
    label = "LinkedIn page"
    message_limit = 3000
    hashtag_limit = 3
    min_interval = 600
    daily_max = 20
    fields = (
        CredentialField(
            "author_urn",
            "Organization URN",
            "urn:li:organization:<your page id> (the page you administer).",
            store="option",
            placeholder="urn:li:organization:123456",
        ),
        CredentialField(
            "access_token",
            "Access token",
            "Token with w_organization_social (Community Management API).",
        ),
    )
    legacy_secret_field = "access_token"
    how_to = "linkedin.com/developers → create an app → request Community Management API → authorise w_organization_social and paste the token."
    endpoints = ("POST api.linkedin.com/rest/posts (article share)",)
    supports_check = False

    @classmethod
    def extra_problem(cls, values: Mapping[str, str]) -> str:
        if not values.get("author_urn", "").startswith(
            ("urn:li:organization:", "urn:li:person:")
        ):
            return "The author must be a urn:li:organization:… (or person) URN."
        return ""

    def publish(self, ctx: SendContext) -> Outcome:
        body: dict[str, Any] = {
            "author": ctx.value("author_urn"),
            "commentary": ctx.job.text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if ctx.job.url:
            body["content"] = {
                "article": {"source": ctx.job.url, "title": ctx.job.title[:200]}
            }
        try:
            result = ctx.http.call(
                "https://api.linkedin.com/rest/posts",
                headers={
                    **JSON_HEADERS,
                    **bearer(ctx.value("access_token")),
                    "LinkedIn-Version": LINKEDIN_VERSION,
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                body=json_body(body),
                hosts=_host("api.linkedin.com"),
            )
        except StepError as error:
            return error.outcome
        outcome = classify_status(result.status, result.headers, result.body)
        if not outcome.ok:
            return replace(outcome, detail=ctx.http.scrub(outcome.detail))
        return replace(outcome, external_id=result.headers.get("x-restli-id", ""))


# --------------------------------------------------------------- reddit ----


@register
class RedditAdapter(Adapter):
    kind = KIND_REDDIT
    label = "Reddit (subreddit link post)"
    message_limit = 300
    hashtag_limit = 0
    emoji_ok = False
    min_interval = 900
    daily_max = 5
    fields = (
        CredentialField(
            "subreddit",
            "Subreddit",
            "Without r/ — a community you moderate or whose rules allow your posts.",
            store="option",
            placeholder="androidflashing",
        ),
        CredentialField(
            "client_id",
            "App client id",
            "reddit.com/prefs/apps → create app (web app).",
        ),
        CredentialField("client_secret", "App secret", "Shown next to the app."),
        CredentialField(
            "refresh_token",
            "Refresh token",
            "Obtained once with the authorization-code flow (duration=permanent, scope submit identity).",
        ),
    )
    how_to = "reddit.com/prefs/apps → create a web app → authorise once with duration=permanent and scope “submit identity” → paste the refresh token. Respect the subreddit rules."
    endpoints = (
        "POST www.reddit.com/api/v1/access_token (refresh)",
        "POST oauth.reddit.com/api/submit (kind=link)",
    )

    def _token(self, ctx: SendContext) -> str:
        return refresh_access_token(
            ctx,
            "https://www.reddit.com/api/v1/access_token",
            client_id=ctx.value("client_id"),
            client_secret=ctx.value("client_secret"),
            refresh_token=ctx.value("refresh_token"),
            basic=True,
            user_agent=REDDIT_UA,
        )

    def publish(self, ctx: SendContext) -> Outcome:
        try:
            token = self._token(ctx)
            result = ctx.http.call(
                "https://oauth.reddit.com/api/submit",
                headers={**FORM_HEADERS, **bearer(token), "User-Agent": REDDIT_UA},
                body=form_body(
                    {
                        "sr": ctx.value("subreddit").removeprefix("r/"),
                        "kind": "link",
                        "title": (ctx.job.title or first_line(ctx.job.text, 300))[:300],
                        "url": ctx.job.url,
                        "api_type": "json",
                        "resubmit": "true" if ctx.job.reshare_round else "false",
                    }
                ),
                hosts=_host("oauth.reddit.com"),
            )
        except StepError as error:
            return error.outcome
        outcome = classify_status(result.status, result.headers, result.body)
        if not outcome.ok:
            return replace(outcome, detail=ctx.http.scrub(outcome.detail))
        try:
            data = json.loads(result.body or b"{}").get("json", {})
        except (ValueError, AttributeError):
            data = {}
        errors = data.get("errors") or []
        if errors:
            first = errors[0] if isinstance(errors[0], list) else [str(errors[0])]
            code, message = (first + ["", ""])[:2]
            transient = str(code).upper() in {"RATELIMIT", "TOO_MANY_REQUESTS"}
            return Outcome(
                False,
                transient,
                "reddit",
                f"Reddit: {code} {message}"[:280],
                retry_after=600 if transient else 0,
            )
        inner = data.get("data") or {}
        return Outcome(True, False, "200", external_id=_id(inner, "name"))

    def check(self, ctx: SendContext) -> Outcome:
        try:
            token = self._token(ctx)
            data = ctx.http.json(
                "https://oauth.reddit.com/api/v1/me",
                method="GET",
                headers={**bearer(token), "User-Agent": REDDIT_UA},
                hosts=_host("oauth.reddit.com"),
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", f"Signed in as u/{data.get('name', '')}")


# ------------------------------------------------------------ pinterest ----


@register
class PinterestAdapter(Adapter):
    kind = KIND_PINTEREST
    label = "Pinterest board (pin)"
    media = MEDIA_IMAGE
    message_limit = 800
    hashtag_limit = 3
    min_interval = 300
    daily_max = 50
    fields = (
        CredentialField(
            "board_id",
            "Board id",
            "GET /v5/boards lists your boards with their ids.",
            store="option",
        ),
        CredentialField(
            "access_token",
            "Access token",
            "OAuth token with boards:read and pins:write.",
        ),
    )
    legacy_secret_field = "access_token"
    how_to = "developers.pinterest.com → your app → generate an access token with boards:read, pins:write."
    endpoints = ("POST api.pinterest.com/v5/pins (image_url source)",)

    def publish(self, ctx: SendContext) -> Outcome:
        try:
            data = ctx.http.json(
                "https://api.pinterest.com/v5/pins",
                headers={**JSON_HEADERS, **bearer(ctx.value("access_token"))},
                body=json_body(
                    {
                        "board_id": ctx.value("board_id"),
                        "title": first_line(ctx.job.text, 100),
                        "description": ctx.job.text[:800],
                        "link": ctx.job.url,
                        "media_source": {
                            "source_type": "image_url",
                            "url": ctx.media.vertical_url or ctx.media.image_url,
                        },
                    }
                ),
                hosts=_host("api.pinterest.com"),
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", external_id=_id(data))

    def check(self, ctx: SendContext) -> Outcome:
        try:
            data = ctx.http.json(
                "https://api.pinterest.com/v5/user_account",
                method="GET",
                headers=bearer(ctx.value("access_token")),
                hosts=_host("api.pinterest.com"),
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", f"Account: {data.get('username', '')}")


# --------------------------------------------------------------- tiktok ----


@register
class TikTokAdapter(Adapter):
    kind = KIND_TIKTOK
    label = "TikTok (Content Posting API)"
    media = MEDIA_VIDEO
    message_limit = 2200
    hashtag_limit = 4
    min_interval = 1800
    daily_max = 6
    fields = (
        CredentialField(
            "privacy_level",
            "Privacy",
            "Apps that TikTok has not audited can only post privately (SELF_ONLY).",
            store="option",
            required=False,
            choices=(
                ("SELF_ONLY", "Only me (unaudited apps)"),
                ("PUBLIC_TO_EVERYONE", "Public (audited apps)"),
            ),
        ),
        CredentialField(
            "access_token",
            "Access token",
            "User token with the video.publish scope.",
        ),
    )
    legacy_secret_field = "access_token"
    how_to = "developers.tiktok.com → create an app → Content Posting API → authorise the account (video.publish) and verify the site's media URL prefix."
    endpoints = (
        "POST open.tiktokapis.com/v2/post/publish/video/init/ (PULL_FROM_URL)",
    )

    def publish(self, ctx: SendContext) -> Outcome:
        try:
            data = ctx.http.json(
                "https://open.tiktokapis.com/v2/post/publish/video/init/",
                headers={
                    "Content-Type": "application/json; charset=UTF-8",
                    "Accept": "application/json",
                    **bearer(ctx.value("access_token")),
                },
                body=json_body(
                    {
                        "post_info": {
                            "title": ctx.job.text[:2200],
                            "privacy_level": ctx.value("privacy_level") or "SELF_ONLY",
                            "disable_comment": False,
                        },
                        "source_info": {
                            "source": "PULL_FROM_URL",
                            "video_url": ctx.media.video_url,
                        },
                    }
                ),
                hosts=_host("open.tiktokapis.com"),
            )
        except StepError as error:
            return error.outcome
        error_block = data.get("error") or {}
        if error_block.get("code") not in {None, "", "ok"}:
            return Outcome(
                False,
                False,
                "tiktok",
                f"TikTok: {error_block.get('message', error_block.get('code'))}"[:280],
            )
        inner = data.get("data") or {}
        return Outcome(True, False, "200", external_id=_id(inner, "publish_id"))

    def check(self, ctx: SendContext) -> Outcome:
        try:
            ctx.http.json(
                "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
                headers={
                    "Content-Type": "application/json; charset=UTF-8",
                    **bearer(ctx.value("access_token")),
                },
                body=b"{}",
                hosts=_host("open.tiktokapis.com"),
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", "Creator account is reachable")


# -------------------------------------------------------------- youtube ----


@register
class YouTubeAdapter(Adapter):
    kind = KIND_YOUTUBE
    label = "YouTube (Shorts upload)"
    media = MEDIA_VIDEO
    message_limit = 5000
    hashtag_limit = 3
    min_interval = 3600
    daily_max = 6
    fields = (
        CredentialField(
            "client_id",
            "OAuth client id",
            "Google Cloud → APIs & Services → Credentials.",
        ),
        CredentialField(
            "client_secret",
            "OAuth client secret",
            "Shown next to the client id in the same credentials screen.",
        ),
        CredentialField(
            "refresh_token",
            "Refresh token",
            "Obtained once for the channel owner with scope youtube.upload (offline access).",
        ),
    )
    how_to = "console.cloud.google.com → enable YouTube Data API v3 → OAuth client → authorise the channel once with youtube.upload and offline access → paste the refresh token. Unaudited projects upload as private until Google audits the app. Community posts have no API, so videos (Shorts) are what can be published."
    endpoints = (
        "POST oauth2.googleapis.com/token (refresh)",
        "POST www.googleapis.com/upload/youtube/v3/videos?uploadType=multipart",
    )

    def _token(self, ctx: SendContext) -> str:
        return refresh_access_token(
            ctx,
            "https://oauth2.googleapis.com/token",
            client_id=ctx.value("client_id"),
            client_secret=ctx.value("client_secret"),
            refresh_token=ctx.value("refresh_token"),
            basic=False,
        )

    def publish(self, ctx: SendContext) -> Outcome:
        try:
            video = ctx.media.video_bytes()
        except ChannelConfigError as error:
            return Outcome(False, False, "media", str(error))
        if len(video) > MAX_UPLOAD_BYTES:
            return Outcome(
                False, False, "media", "The generated video is too large to upload."
            )
        title = first_line(ctx.job.text, 90)
        if "#shorts" not in title.lower():
            title = f"{title} #Shorts"
        metadata = json_body(
            {
                "snippet": {
                    "title": title[:100],
                    "description": ctx.job.text[:5000],
                    "categoryId": "28",
                    "tags": re.findall(r"#(\w+)", ctx.job.text)[:15],
                },
                "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
            }
        )
        digest = hashlib.sha256(ctx.job.idempotency_key.encode()).hexdigest()[:16]
        boundary = f"mobofirm{digest}"
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + metadata
            + f"\r\n--{boundary}\r\nContent-Type: video/mp4\r\n\r\n".encode()
            + video
            + f"\r\n--{boundary}--\r\n".encode()
        )
        try:
            token = self._token(ctx)
            data = ctx.http.long().json(
                "https://www.googleapis.com/upload/youtube/v3/videos"
                "?uploadType=multipart&part=snippet,status",
                headers={
                    "Content-Type": f"multipart/related; boundary={boundary}",
                    "Accept": "application/json",
                    **bearer(token),
                },
                body=body,
                hosts=_host("www.googleapis.com"),
            )
        except StepError as error:
            return error.outcome
        return Outcome(True, False, "200", external_id=_id(data))

    def check(self, ctx: SendContext) -> Outcome:
        try:
            token = self._token(ctx)
            data = ctx.http.json(
                "https://www.googleapis.com/youtube/v3/channels?part=id,snippet&mine=true",
                method="GET",
                headers=bearer(token),
                hosts=_host("www.googleapis.com"),
            )
        except StepError as error:
            return error.outcome
        items = data.get("items") or [{}]
        title = (items[0].get("snippet") or {}).get("title", "")
        return Outcome(
            True, False, "200", f"Channel: {title}" if title else "Connected"
        )


# ------------------------------------------------------------- whatsapp ----


@register
class WhatsAppAdapter(Adapter):
    kind = KIND_WHATSAPP
    label = "WhatsApp (Cloud API broadcast)"
    message_limit = 1000
    min_interval = 3600
    daily_max = 3
    fields = (
        CredentialField(
            "phone_number_id",
            "Phone number id",
            "Meta Business → WhatsApp → API setup.",
            store="option",
        ),
        CredentialField(
            "recipients",
            "Opted-in recipients",
            "Comma-separated international numbers of people who subscribed to updates (max 50).",
            store="option",
            placeholder="+15551234567, +4915112345678",
        ),
        CredentialField(
            "template_name",
            "Approved template",
            "A utility/marketing template with two body variables: {{1}} title, {{2}} link.",
            store="option",
        ),
        CredentialField(
            "language",
            "Template language",
            "For example en_US.",
            store="option",
            required=False,
        ),
        CredentialField(
            "access_token",
            "System-user access token",
            "Token with whatsapp_business_messaging.",
        ),
    )
    legacy_secret_field = "access_token"
    how_to = "WhatsApp Channels (newsletters) have no posting API. This sends a pre-approved template message through the Cloud API to people who opted in; create and get the template approved in Meta Business Manager first."
    endpoints = (
        f"POST {GRAPH_HOST}/{GRAPH_VERSION}/<phone id>/messages (template, one per recipient)",
    )

    @classmethod
    def extra_problem(cls, values: Mapping[str, str]) -> str:
        numbers = _numbers(values.get("recipients", ""))
        if not numbers:
            return "Add at least one opted-in recipient number."
        if len(numbers) > 50:
            return "At most 50 recipients per broadcast."
        return ""

    def publish(self, ctx: SendContext) -> Outcome:
        done: list[str] = list(ctx.state.get("sent_to", []))
        title = re.sub(r"\s+", " ", ctx.job.title)[:200]
        for number in _numbers(ctx.value("recipients")):
            if number in done:
                continue
            payload = {
                "messaging_product": "whatsapp",
                "to": number,
                "type": "template",
                "template": {
                    "name": ctx.value("template_name"),
                    "language": {"code": ctx.value("language") or "en_US"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": title},
                                {"type": "text", "text": ctx.job.url},
                            ],
                        }
                    ],
                },
            }
            try:
                ctx.http.json(
                    _graph(f"{ctx.value('phone_number_id')}/messages"),
                    headers={**JSON_HEADERS, **bearer(ctx.value("access_token"))},
                    body=json_body(payload),
                    hosts=_host(GRAPH_HOST),
                )
            except StepError as error:
                ctx.state["sent_to"] = done
                return error.outcome
            done.append(number)
        ctx.state["sent_to"] = done
        return Outcome(True, False, "200", external_id=f"{len(done)} recipients")

    def check(self, ctx: SendContext) -> Outcome:
        try:
            data = ctx.http.json(
                _graph(f"{ctx.value('phone_number_id')}?fields=display_phone_number"),
                method="GET",
                headers=bearer(ctx.value("access_token")),
                hosts=_host(GRAPH_HOST),
            )
        except StepError as error:
            return error.outcome
        return Outcome(
            True, False, "200", f"Number: {data.get('display_phone_number', '')}"
        )


def _numbers(raw: str) -> list[str]:
    found = [re.sub(r"[^\d+]", "", part) for part in re.split(r"[,\s]+", raw) if part]
    return [n for n in found if re.fullmatch(r"\+?\d{8,15}", n)]
