"""The platform catalogue behind the "Add a channel" menu.

Pure presentation metadata (no model, no network): which menu a platform sits in, how it
authenticates, how hard the setup is, and the step-by-step way to get its credentials with
the official documentation links. What a platform can *do* (limits, media, fields) stays
on its adapter in ``platforms``; a test keeps this catalogue and the registry in step.

Icons are Lucide only. Lucide has no brand logos, so each platform gets a generic glyph
that hints at its kind of content, never an imitation of a trademark.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.distribution import channels
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
    KIND_WHATSAPP_CHANNEL,
    KIND_X,
    KIND_YOUTUBE,
)

SOCIAL = "social"
VIDEO = "video"
MESSAGING = "messaging"
FEDIVERSE = "fediverse"
DEVELOPER = "developer"

EASY = "easy"
MEDIUM = "medium"
HARD = "hard"
DIFFICULTY_LABELS = {EASY: "Easy", MEDIUM: "Medium", HARD: "Advanced"}
DIFFICULTY_TONES = {EASY: "success", MEDIUM: "warning", HARD: "danger"}

MODE_API = "api"
MODE_ASSISTED = "assisted"


@dataclass(frozen=True, slots=True)
class Category:
    key: str
    label: str
    icon: str
    blurb: str


@dataclass(frozen=True, slots=True)
class Info:
    """Catalogue entry of one platform."""

    kind: str
    categories: tuple[str, ...]
    icon: str
    auth: str
    difficulty: str
    summary: str
    steps: tuple[str, ...]
    docs: tuple[tuple[str, str], ...]
    mode: str = MODE_API
    notes: tuple[str, ...] = ()


CATEGORIES = (
    Category(SOCIAL, "Social networks", "share-2", "Public feeds and pages."),
    Category(VIDEO, "Video", "video", "Short vertical video platforms."),
    Category(
        MESSAGING,
        "Messaging & communities",
        "message-circle",
        "Chats, groups and forums your audience already uses.",
    ),
    Category(
        FEDIVERSE, "Fediverse & open", "globe", "Open protocols without a single owner."
    ),
    Category(
        DEVELOPER, "Developer & automation", "webhook", "Your own tools and workflows."
    ),
)
CATEGORY_BY_KEY = {category.key: category for category in CATEGORIES}

_META_DOCS = "https://developers.facebook.com/docs/"

INFO: dict[str, Info] = {
    KIND_FACEBOOK: Info(
        KIND_FACEBOOK,
        (SOCIAL,),
        "share-2",
        "Page access token (Meta app)",
        MEDIUM,
        "Posts links and text to a Facebook Page you manage.",
        (
            "Create an app at developers.facebook.com and add the Pages use case.",
            "In Graph API Explorer, generate a User token with pages_manage_posts and pages_read_engagement.",
            "Exchange it for a long-lived Page access token and copy the Page id.",
            "Paste both here, then use “Test connection”.",
        ),
        (("Pages API: posts", _META_DOCS + "pages-api/posts"),),
    ),
    KIND_INSTAGRAM: Info(
        KIND_INSTAGRAM,
        (SOCIAL,),
        "camera",
        "Access token (Meta app, Professional account)",
        HARD,
        "Publishes image posts (a share card is generated) to an Instagram Professional account.",
        (
            "Convert the account to Professional and link it to a Facebook Page.",
            "In your Meta app add the Instagram content publishing permission.",
            "Generate a token with instagram_content_publish and copy the Instagram business account id.",
            "Paste both here, then use “Test connection”.",
        ),
        (
            (
                "Content publishing",
                _META_DOCS + "instagram-platform/content-publishing",
            ),
        ),
    ),
    KIND_THREADS: Info(
        KIND_THREADS,
        (SOCIAL,),
        "at-sign",
        "User access token (Threads API)",
        MEDIUM,
        "Posts short text to a Threads profile.",
        (
            "In your Meta app add the Threads API use case.",
            "Generate a user token with threads_basic and threads_content_publish.",
            "Copy the Threads user id and paste both here.",
        ),
        (("Threads posts", _META_DOCS + "threads/posts"),),
    ),
    KIND_X: Info(
        KIND_X,
        (SOCIAL,),
        "hash",
        "OAuth 2.0 user tokens",
        HARD,
        "Posts to an X account; every link counts as 23 characters.",
        (
            "Create a project and app at developer.x.com with write permission.",
            "Enable OAuth 2.0 with tweet.write and offline.access.",
            "Authorise the account once and paste the user token, refresh token, client id and secret.",
        ),
        (("Create a post", "https://docs.x.com/x-api/posts/create-post"),),
    ),
    KIND_LINKEDIN: Info(
        KIND_LINKEDIN,
        (SOCIAL,),
        "users",
        "OAuth token (Community Management API)",
        HARD,
        "Posts to a LinkedIn organisation page.",
        (
            "Create an app at linkedin.com/developers and request the Community Management API.",
            "Authorise with w_organization_social.",
            "Paste the organisation URN (urn:li:organization:…) and the access token.",
        ),
        (
            (
                "Posts API",
                "https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api",
            ),
        ),
    ),
    KIND_PINTEREST: Info(
        KIND_PINTEREST,
        (SOCIAL,),
        "pin",
        "OAuth access token",
        MEDIUM,
        "Creates a pin (a share card) on one of your boards.",
        (
            "Create an app at developers.pinterest.com.",
            "Generate an access token with boards:read and pins:write.",
            "Copy the board id and paste both here.",
        ),
        (("Pinterest API v5", "https://developers.pinterest.com/docs/api/v5/"),),
    ),
    KIND_TIKTOK: Info(
        KIND_TIKTOK,
        (SOCIAL, VIDEO),
        "music",
        "OAuth access token (Content Posting API)",
        HARD,
        "Uploads the generated vertical video. Posts stay private until TikTok audits your app.",
        (
            "Create an app at developers.tiktok.com and add the Content Posting API.",
            "Verify the site's media URL prefix in TikTok.",
            "Authorise the account with video.publish and paste the access token.",
        ),
        (
            (
                "Content Posting API",
                "https://developers.tiktok.com/doc/content-posting-api-get-started",
            ),
        ),
        notes=("Needs video tooling on the server.",),
    ),
    KIND_YOUTUBE: Info(
        KIND_YOUTUBE,
        (VIDEO,),
        "circle-play",
        "Google OAuth client + refresh token",
        HARD,
        "Uploads the generated vertical video as a Short. YouTube has no community-post API.",
        (
            "In Google Cloud enable the YouTube Data API v3.",
            "Create an OAuth client and authorise the channel once with youtube.upload.",
            "Paste the client id, client secret and refresh token.",
        ),
        (
            (
                "videos.insert",
                "https://developers.google.com/youtube/v3/docs/videos/insert",
            ),
        ),
        notes=("Unaudited Google projects upload as private.",),
    ),
    KIND_TELEGRAM: Info(
        KIND_TELEGRAM,
        (MESSAGING,),
        "send",
        "Bot token",
        EASY,
        "Posts to a Telegram channel or group through your own bot.",
        (
            "Talk to @BotFather, send /newbot and copy the token.",
            "Add the bot as an administrator of your channel or group.",
            "Enter the @channelname (or chat id) and the token.",
        ),
        (("Bot API: sendMessage", "https://core.telegram.org/bots/api#sendmessage"),),
    ),
    KIND_WHATSAPP: Info(
        KIND_WHATSAPP,
        (MESSAGING,),
        "message-circle",
        "Meta system-user token (WhatsApp Cloud API)",
        HARD,
        "Official API: messages people who opted in, using an approved template (or plain text inside their 24 hour window).",
        (
            "In Meta Business Manager create a WhatsApp Business Account and a business number.",
            "Create a message template with two body variables ({{1}} title, {{2}} link) and wait for approval.",
            "Create a system user with whatsapp_business_messaging and copy its token and the phone number id.",
            "List only people who agreed to receive messages; anyone who replies STOP is removed for good.",
            "Optional: add the webhook URL shown after saving to get delivery status and STOP/START replies.",
        ),
        (
            (
                "Send message templates",
                _META_DOCS + "whatsapp/cloud-api/guides/send-message-templates",
            ),
            ("Webhooks", _META_DOCS + "whatsapp/cloud-api/webhooks"),
            ("Business messaging policy", "https://business.whatsapp.com/policy"),
        ),
        notes=(
            "Needs a prior opt-in per person.",
            "Outside the 24 hour window only approved templates may be sent.",
            "Meta limits how many people you may message per day and may throttle marketing templates.",
        ),
    ),
    KIND_WHATSAPP_CHANNEL: Info(
        KIND_WHATSAPP_CHANNEL,
        (MESSAGING,),
        "megaphone",
        "None (posted by hand)",
        EASY,
        "Assisted: WhatsApp Channels have no posting API, so the post is prepared and you paste it.",
        (
            "Open your WhatsApp Channel, choose Share and copy the invite link.",
            "Paste the link here and save.",
            "When new content is ready it appears under “Hand-off” with copy-ready text and the link.",
            "Paste it in WhatsApp, then press “Mark as posted”.",
        ),
        (("WhatsApp Channels", "https://www.whatsapp.com/channels/"),),
        mode=MODE_ASSISTED,
        notes=("Nothing is sent automatically.",),
    ),
    KIND_DISCORD: Info(
        KIND_DISCORD,
        (MESSAGING,),
        "bot",
        "Webhook URL",
        EASY,
        "Posts to a Discord channel through a webhook.",
        (
            "Channel settings, Integrations, Webhooks, New webhook.",
            "Copy the webhook URL and paste it here.",
        ),
        (
            (
                "Webhook resource",
                "https://discord.com/developers/docs/resources/webhook",
            ),
        ),
    ),
    KIND_SLACK: Info(
        KIND_SLACK,
        (MESSAGING,),
        "message-square-text",
        "Incoming webhook URL",
        EASY,
        "Posts to a Slack channel through an incoming webhook.",
        (
            "Create an app at api.slack.com/apps.",
            "Enable Incoming Webhooks and add one to a workspace channel.",
            "Paste the webhook URL here.",
        ),
        (("Incoming webhooks", "https://api.slack.com/messaging/webhooks"),),
    ),
    KIND_REDDIT: Info(
        KIND_REDDIT,
        (MESSAGING,),
        "rss",
        "OAuth refresh token (web app)",
        HARD,
        "Submits a link post to a subreddit. Respect that subreddit's rules.",
        (
            "Create a web app at reddit.com/prefs/apps.",
            "Authorise once with duration=permanent and scope submit and identity.",
            "Paste the subreddit, client id, secret and refresh token.",
        ),
        (("Reddit API", "https://www.reddit.com/dev/api/"),),
    ),
    KIND_MASTODON: Info(
        KIND_MASTODON,
        (FEDIVERSE,),
        "globe",
        "Access token (your instance)",
        EASY,
        "Posts a status to a Mastodon account.",
        (
            "On your instance open Preferences, Development, New application.",
            "Give it the write:statuses scope and copy “Your access token”.",
            "Enter the instance URL and the token.",
        ),
        (
            (
                "Statuses API",
                "https://docs.joinmastodon.org/methods/statuses/",
            ),
        ),
    ),
    KIND_BLUESKY: Info(
        KIND_BLUESKY,
        (FEDIVERSE,),
        "cloud",
        "Handle + app password",
        EASY,
        "Posts to a Bluesky account.",
        (
            "In Bluesky open Settings, Privacy and security, App passwords.",
            "Add an app password and copy it.",
            "Enter your handle, the server (bsky.social unless self-hosted) and the app password.",
        ),
        (("Get started", "https://docs.bsky.app/docs/get-started"),),
    ),
    KIND_WEBHOOK: Info(
        KIND_WEBHOOK,
        (DEVELOPER,),
        "webhook",
        "Signing secret (HMAC)",
        EASY,
        "Sends signed JSON to your own endpoint: Zapier, Make, n8n or code.",
        (
            "Create an https endpoint that accepts a JSON POST.",
            "Enter its address and, optionally, a signing secret.",
            "Verify the X-Signature header (HMAC-SHA256 of the body) on your side.",
        ),
        (),
    ),
}

STATE_LABELS = {
    "ready": ("Ready", "success"),
    "failing": ("Failing", "danger"),
    "misconfigured": ("Needs setup", "warning"),
    "off": ("Off", "neutral"),
}


def info_for(kind: str) -> Info | None:
    return INFO.get(kind)


@dataclass(frozen=True, slots=True)
class Card:
    """One picker card: the adapter's facts, the catalogue entry, existing channels."""

    kind: str
    adapter: channels.Adapter
    info: Info | None
    icon: str
    categories: tuple[str, ...]
    assisted: bool
    difficulty_label: str
    difficulty_tone: str
    existing: list[dict[str, str]]
    search: str

    @property
    def connected(self) -> bool:
        return bool(self.existing)


@dataclass(frozen=True, slots=True)
class Section:
    category: Category
    cards: list[Card]

    @property
    def count(self) -> int:
        return len(self.cards)


def card_for(
    kind: str, adapter: channels.Adapter, existing: list[dict[str, str]]
) -> Card:
    entry = INFO.get(kind)
    categories = entry.categories if entry else (DEVELOPER,)
    return Card(
        kind=kind,
        adapter=adapter,
        info=entry,
        icon=entry.icon if entry else "plug",
        categories=categories,
        assisted=bool(entry and entry.mode == MODE_ASSISTED),
        difficulty_label=DIFFICULTY_LABELS.get(entry.difficulty, "") if entry else "",
        difficulty_tone=DIFFICULTY_TONES.get(entry.difficulty, "neutral")
        if entry
        else "neutral",
        existing=existing,
        search=" ".join(
            [
                adapter.label,
                kind,
                entry.auth if entry else "",
                entry.summary if entry else "",
                " ".join(CATEGORY_BY_KEY[c].label for c in categories),
            ]
        ).lower(),
    )


def group(cards: list[Card]) -> list[Section]:
    """The menu: categories in their fixed order, each with its cards by label.

    A platform listed under two categories (TikTok) appears in both.
    """
    return [
        Section(
            category,
            sorted(
                (c for c in cards if category.key in c.categories),
                key=lambda c: c.adapter.label,
            ),
        )
        for category in CATEGORIES
    ]


def matches(card: Card, query: str) -> bool:
    """Case-insensitive match of every word of ``query`` against the card's search text."""
    return all(word in card.search for word in query.lower().split())
