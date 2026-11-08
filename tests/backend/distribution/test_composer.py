"""Composer: hook-first copy, hashtags, per-platform limits, emoji policy, honesty rules."""

from __future__ import annotations

import pytest

from apps.distribution import channels, composer
from apps.distribution.channels import visible_length
from apps.distribution.constants import (
    EMOJI_EXPRESSIVE,
    EMOJI_MINIMAL,
    EMOJI_NONE,
    HOOK_BENEFIT,
    HOOK_EVERGREEN,
    HOOK_HOWTO,
    HOOK_QUESTION,
    HOOK_URGENCY,
)
from apps.distribution.content import Content

LINK = "https://mobofirm.example/go/abc2345/"


def firmware(**extra) -> Content:
    base = {
        "kind": "firmware",
        "object_ref": "official:1",
        "title": "Acme Phone One Global",
        "url": "https://mobofirm.example/firmwares/file/official/1/",
        "brand": "Acme",
        "model": "Phone One",
        "variant": "Global",
        "chipset": "Mediatek Helio G100 Ultra (6nm)",
        "android_version": "14",
        "build_number": "B123",
        "size_bytes": 3 * 1024**3,
    }
    return Content(**{**base, **extra})


def post(**extra) -> Content:
    base = {
        "kind": "blog",
        "object_ref": "post:1",
        "title": "How to unbrick a MediaTek phone",
        "url": "https://mobofirm.example/blog/unbrick/",
        "summary": "Step by step recovery for bootlooping MediaTek devices.",
        "tags": ("MediaTek", "Unbrick"),
        "topic": "MediaTek",
    }
    return Content(**{**base, **extra})


def adapter_for(kind: str) -> channels.Adapter:
    found = channels.get_adapter(kind)
    assert found is not None, kind
    return found


def write(
    item: Content,
    kind: str = "mastodon",
    *,
    hook: str = HOOK_QUESTION,
    emoji: str = EMOJI_MINIMAL,
    hashtag_cap: int = 0,
    evergreen: bool = False,
) -> composer.Draft:
    return composer.compose(
        item,
        adapter_for(kind),
        hook=hook,
        link=LINK,
        site="Mobofirm",
        emoji=emoji,
        hashtag_cap=hashtag_cap,
        evergreen=evergreen,
    )


def test_hashtags_are_sanitised_bounded_and_unique():
    assert composer.to_hashtag("Galaxy S23 Ultra") == "GalaxyS23Ultra"
    assert composer.to_hashtag("Helio G100 (6nm)") == "HelioG100"
    assert composer.to_hashtag("2024") == "" and composer.to_hashtag("  ") == ""
    tags = composer.build_hashtags(firmware(tags=("Acme", "acme", "Phone One")), 5)
    assert tags[0] == "#Acme" and "#PhoneOne" in tags
    assert len({t.lower() for t in tags}) == len(tags)
    assert composer.build_hashtags(firmware(), 0) == []
    assert len(composer.build_hashtags(firmware(), 2)) == 2


def test_trending_launches_get_extra_tags_and_a_new_release_tag():
    plain = composer.build_hashtags(firmware(), 2)
    boosted = composer.build_hashtags(firmware(), 2, trending=True)
    assert len(boosted) == 4 and "#NewRelease" in boosted and "#NewRelease" not in plain


def test_every_hook_leads_with_the_hook_then_a_detail_then_the_link():
    for hook in (HOOK_QUESTION, HOOK_BENEFIT, HOOK_URGENCY, HOOK_HOWTO):
        draft = write(firmware(), hook=hook)
        lines = draft.text.split("\n")
        assert draft.hook == hook
        assert lines[0].startswith(composer.HOOK_EMOJI[hook])
        assert "Acme" in lines[0]
        assert LINK in lines and lines.index(LINK) >= 1
        assert draft.text.count(LINK) == 1


def test_the_copy_is_honest_about_freshness_and_price():
    """No 'free' claims; 'just added' only on first shares; re-shares say so."""
    for hook in (HOOK_QUESTION, HOOK_BENEFIT, HOOK_URGENCY, HOOK_HOWTO):
        assert "free" not in write(firmware(), hook=hook).text.lower()
    assert "Just added" in write(firmware(), hook=HOOK_URGENCY).text
    again = write(firmware(), hook=HOOK_URGENCY, evergreen=True)
    assert again.hook == HOOK_EVERGREEN
    assert "Just added" not in again.text and "Still useful" in again.text
    launch = write(firmware(trending=True), hook=HOOK_URGENCY)
    assert "New on Mobofirm" in launch.text


def test_howto_mentions_a_guide_only_when_one_exists():
    with_guide = write(firmware(has_guide=True), hook=HOOK_HOWTO).text
    without = write(firmware(), hook=HOOK_HOWTO).text
    assert "step-by-step guide" in with_guide
    assert "guide" not in without


def test_gated_firmware_is_a_teaser_without_download_language():
    text = write(firmware(gated=True), hook=HOOK_BENEFIT).text.lower()
    assert "featured file" in text and "download" not in text


def test_gated_blog_posts_only_show_a_short_teaser():
    long_summary = "Secret details. " * 40
    draft = write(post(gated=True, summary=long_summary[:140]), hook=HOOK_BENEFIT)
    assert "Featured article: preview" in draft.text
    assert len(draft.text) < 600


def test_emoji_policy_none_minimal_expressive():
    none = write(firmware(), emoji=EMOJI_NONE).text
    minimal = write(firmware(), emoji=EMOJI_MINIMAL).text
    expressive = write(firmware(), emoji=EMOJI_EXPRESSIVE).text
    assert "❓" not in none and "👉" not in none
    assert "❓" in minimal and "👉" not in minimal
    assert "❓" in expressive and f"👉 {LINK}" in expressive
    reddit = write(firmware(), kind="reddit", emoji=EMOJI_EXPRESSIVE).text
    assert "❓" not in reddit and "👉" not in reddit


def test_blog_hooks_use_topic_and_title():
    assert "Ever wondered about MediaTek?" in write(post(), hook=HOOK_QUESTION).text
    assert (
        write(post(), hook=HOOK_HOWTO)
        .text.splitlines()[0]
        .endswith("unbrick a MediaTek phone")
    )
    assert "From the archive" in write(post(), hook=HOOK_URGENCY, evergreen=True).text


@pytest.mark.parametrize("kind", sorted(channels.adapters()))
def test_posts_fit_every_platform_and_keep_the_link(kind):
    adapter = adapter_for(kind)
    item = firmware(
        title="Acme " * 60,
        brand="Acme " * 20,
        model="Phone One " * 20,
        summary="s" * 500,
    )
    draft = write(item, kind=kind, hook=HOOK_BENEFIT)
    assert visible_length(draft.text, adapter.url_length) <= adapter.message_limit
    assert LINK in draft.text
    assert len(draft.hashtags) <= max(adapter.hashtag_limit, 0)


def test_hashtag_cap_overrides_the_platform_default_but_never_adds_to_zero_platforms():
    capped = write(firmware(), kind="instagram", hashtag_cap=1)
    assert len(capped.hashtags) == 1
    assert write(firmware(), kind="slack").hashtags == ()
    assert write(firmware(), kind="reddit", hashtag_cap=3).hashtags != ()


def test_x_counts_links_as_23_so_longer_copy_still_fits():
    draft = write(firmware(), kind="x", hook=HOOK_BENEFIT)
    assert visible_length(draft.text, 23) <= 280
    assert visible_length(draft.text, 23) == len(draft.text) - (len(LINK) - 23)
