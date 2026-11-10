"""The shared share component: link building, escaping and the template tag."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from django.template import Context, Template
from django.test import RequestFactory
from django.utils.html import escape

from apps.core.share import ShareLink, is_shareable_url, share_links

URL = "https://example.org/blog/a-guide/?a=1&b=2#part"
TITLE = 'Flashing "Atlas" & <friends> 100% — ünï'


def by_label() -> dict[str, ShareLink]:
    return {link.label: link for link in share_links(URL, TITLE)}


def render(url: str, title: str = "A title", request=None, **options: str) -> str:
    extra = "".join(f' {key}="{value}"' for key, value in options.items())
    template = Template(
        '{% load share_tags %}{% share_widget url title' + extra + " %}"
    )
    return template.render(Context({"url": url, "title": title, "request": request}))


def test_every_requested_network_is_offered_in_order() -> None:
    assert [link.label for link in share_links(URL, TITLE)] == [
        "X",
        "Facebook",
        "WhatsApp",
        "Telegram",
        "LinkedIn",
        "Email",
    ]


def test_the_canonical_url_is_percent_encoded_exactly_once() -> None:
    links = by_label()
    expected = "https%3A%2F%2Fexample.org%2Fblog%2Fa-guide%2F%3Fa%3D1%26b%3D2%23part"

    for label in ("X", "Facebook", "Telegram", "LinkedIn", "Email"):
        assert expected in links[label].href, label
        assert "%253A" not in links[label].href, f"{label} was double-encoded"
    query = parse_qs(urlsplit(links["Facebook"].href).query)
    assert query["u"] == [URL]


def test_titles_with_markup_quotes_and_unicode_cannot_break_out_of_the_query() -> None:
    links = by_label()

    x_query = parse_qs(urlsplit(links["X"].href).query)
    assert x_query["text"] == [TITLE]
    assert x_query["url"] == [URL]
    telegram = parse_qs(urlsplit(links["Telegram"].href).query)
    assert telegram["text"] == [TITLE]
    whatsapp = parse_qs(urlsplit(links["WhatsApp"].href).query)
    assert whatsapp["text"] == [f"{TITLE} {URL}"]
    for link in links.values():
        assert " " not in link.href and '"' not in link.href
        assert "<" not in link.href and ">" not in link.href


def test_email_uses_mailto_with_percent_encoded_subject_and_body() -> None:
    email = by_label()["Email"]

    assert email.href.startswith("mailto:?subject=")
    assert "+" not in email.href
    query = parse_qs(urlsplit(email.href).query)
    assert query["subject"] == [TITLE]
    assert query["body"] == [URL]
    assert not email.is_web


def test_long_titles_are_capped_and_whitespace_collapsed() -> None:
    links = share_links(URL, "word  " * 200)

    text = parse_qs(urlsplit(links[0].href).query)["text"][0]
    assert len(text) <= 200
    assert "  " not in text


def test_unusable_urls_yield_no_links() -> None:
    for bad in (
        "",
        "/relative/path/",
        "javascript:alert(1)",
        "ftp://example.org/x",
        "https:///nohost",
        "https://user:secret@example.org/x",
    ):
        assert share_links(bad, TITLE) == [], bad
        assert not is_shareable_url(bad)


def test_tag_renders_accessible_controls_with_noopener_links() -> None:
    html = render(URL, TITLE, heading="Share this article", copy_message="Copied it.")

    assert 'class="share"' in html and "data-share" in html
    assert 'aria-labelledby="share-' in html
    assert "Share this article" in html
    assert f'data-copy="{escape(URL)}"' in html
    assert 'data-copy-message="Copied it."' in html
    assert "data-share-native-item hidden" in html
    assert html.count('rel="noopener noreferrer"') == 5
    assert html.count('target="_blank"') == 5
    assert "(opens in a new tab)" in html
    assert 'href="mailto:' in html
    mailto_tag = re.search(r'<a [^>]*href="mailto:[^>]*>', html)
    assert mailto_tag is not None
    assert "target" not in mailto_tag.group(0) and "rel=" not in mailto_tag.group(0)


def test_tag_escapes_the_title_in_attributes_and_markup() -> None:
    html = render(URL, '"><script>alert(1)</script>')

    assert "<script>" not in html
    assert 'data-share-title="&quot;&gt;&lt;script&gt;' in html


def test_tag_makes_a_relative_path_absolute_with_the_request() -> None:
    request = RequestFactory().get("/", HTTP_HOST="testserver")

    html = render("/firmwares/official/1/", "Firmware", request=request)

    assert 'data-share-url="http://testserver/firmwares/official/1/"' in html


def test_tag_renders_nothing_for_a_non_http_url() -> None:
    assert render("javascript:alert(1)").strip() == ""
    assert render("").strip() == ""
    assert render("//evil.example/x").strip() == ""
