"""Feature-derived sanitising and the XSS corpus for the rich-text editor."""

from __future__ import annotations

import pytest

from apps.core.editor import ProfileSpec, sanitize_rich_text, text_length
from apps.core.editor.features import ALL_FEATURE_KEYS
from apps.core.editor.sanitizer import render_rich_text
from apps.core.editor.spec import UploadRules, default_profile

FULL = ProfileSpec(key="t", features=ALL_FEATURE_KEYS, upload=UploadRules(True))
BARE = ProfileSpec(key="t", features=frozenset())
LINKS = ProfileSpec(key="t", features=frozenset({"links"}))
IMAGES = ProfileSpec(key="t", features=frozenset({"images"}))

XSS_CORPUS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    '<img src="/media/a.png" onerror="alert(1)">',
    '<a href="javascript:alert(1)">x</a>',
    '<a href="  JaVaScRiPt:alert(1)">x</a>',
    '<a href="java&#x09;script:alert(1)">x</a>',
    '<a href="data:text/html,<script>alert(1)</script>">x</a>',
    '<a href="vbscript:msgbox(1)">x</a>',
    "<svg onload=alert(1)><circle/></svg>",
    "<svg><script>alert(1)</script></svg>",
    "<math><mi xlink:href=javascript:alert(1)>x</mi></math>",
    '<p style="background:url(javascript:alert(1))">x</p>',
    '<p style="width:expression(alert(1))">x</p>',
    '<img src="data:image/svg+xml;base64,PHN2Zz48c2NyaXB0PmFsZXJ0KDEpPC9zY3JpcHQ+">',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<iframe src="javascript:alert(1)"></iframe>',
    '<iframe src="https://evil.example/embed/x"></iframe>',
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
    "<form action=javascript:alert(1)><button>x</button></form>",
    "<object data=javascript:alert(1)></object>",
    "<embed src=javascript:alert(1)>",
    "<base href=javascript:alert(1)//>",
    "<meta http-equiv=refresh content=0;url=javascript:alert(1)>",
    "<style>@import 'javascript:alert(1)';</style>",
    "<<script>alert(1)//<</script>",
    '<div onmouseover="alert(1)">x</div>',
    '<table background="javascript:alert(1)"><tr><td>x</td></tr></table>',
    "<template><script>alert(1)</script></template>",
    '<p class="x" id="y" data-a="b" contenteditable>x</p>',
    "<textarea><script>alert(1)</script></textarea>",
    '<a href="//evil.example" target=_blank onclick=alert(1)>x</a>',
]

FORBIDDEN = (
    "<script",
    "onerror",
    "onload",
    "onclick",
    "onmouseover",
    "javascript:",
    "vbscript:",
    "srcdoc",
    "expression(",
    "data:",
    "<svg",
    "<math",
    "<style",
    "<form",
    "<object",
    "<embed",
    "<meta",
    "<base",
    "style=",
    "contenteditable",
    "evil.example/embed",
)


@pytest.mark.parametrize("payload", XSS_CORPUS)
def test_xss_corpus_is_neutralised_with_every_feature_enabled(payload: str) -> None:
    """No known XSS vector survives even the most permissive profile."""
    cleaned = sanitize_rich_text(payload, FULL).lower()
    for token in FORBIDDEN:
        assert token not in cleaned, (token, cleaned)


@pytest.mark.parametrize("payload", XSS_CORPUS)
def test_xss_output_is_a_fixed_point(payload: str) -> None:
    """Re-sanitising sanitised output changes nothing (no mutation XSS)."""
    once = sanitize_rich_text(payload, FULL)
    assert sanitize_rich_text(once, FULL) == once


def test_disabled_features_remove_their_tags() -> None:
    """A profile without a feature strips the matching markup."""
    html = (
        "<p><strong>b</strong><em>i</em><u>u</u><s>s</s>"
        '<a href="https://x.example">l</a></p>'
        "<ul><li>a</li></ul><table><tr><td>c</td></tr></table>"
    )
    cleaned = sanitize_rich_text(html, BARE)
    for tag in ("<strong", "<em", "<u>", "<s>", "<a ", "<ul", "<li", "<table"):
        assert tag not in cleaned
    assert "<p>" in cleaned


def test_each_feature_enables_exactly_its_tags() -> None:
    """Every feature switch maps to the tags the toolbar produces."""
    cases = {
        "bold": "<strong>x</strong>",
        "italic": "<em>x</em>",
        "underline": "<u>x</u>",
        "strike": "<s>x</s>",
        "script": "<sub>x</sub>",
        "lists": "<ul><li>x</li></ul>",
        "style": "<blockquote>x</blockquote>",
        "tables": "<table><tbody><tr><td>x</td></tr></tbody></table>",
        "hr": "<p>x</p><hr>",
    }
    for feature, html in cases.items():
        enabled = ProfileSpec(key="t", features=frozenset({feature}))
        assert sanitize_rich_text(html, enabled) == html, feature
        assert sanitize_rich_text(html, BARE) != html, feature


def test_images_need_the_feature_and_a_same_origin_source() -> None:
    """Images pass only with the images feature and a same-origin path."""
    ok = '<p><img src="/media/editor/a.png" alt="a"></p>'
    assert sanitize_rich_text(ok, IMAGES) == ok
    assert "<img" not in sanitize_rich_text(ok, BARE)
    for src in ("https://evil.example/a.png", "//evil.example/a.png", "data:x"):
        assert "<img" not in sanitize_rich_text(f'<p>t</p><img src="{src}">', IMAGES)
    assert "<img" not in sanitize_rich_text("<p>t</p><img>", IMAGES)


def test_links_are_nofollow_noopener_and_scheme_limited() -> None:
    """External links get rel, and only safe schemes keep their href."""
    cleaned = sanitize_rich_text('<a href="https://x.example/p">x</a>', LINKS)
    assert 'rel="nofollow noopener"' in cleaned
    assert "href" not in sanitize_rich_text('<a href="ftp://x.example">x</a>', LINKS)
    mail = sanitize_rich_text('<a href="mailto:a@b.example">x</a>', LINKS)
    assert 'href="mailto:a@b.example"' in mail


def test_video_embeds_are_rewritten_and_allow_listed() -> None:
    """Only enabled providers survive, as privacy-friendly sandboxed embeds."""
    features = frozenset({"video"})
    youtube = ProfileSpec(key="t", features=features, video_providers=("youtube",))
    vimeo = ProfileSpec(key="t", features=features, video_providers=("vimeo",))
    off = ProfileSpec(key="t", features=features, video_providers=())
    watch = '<iframe src="https://www.youtube.com/watch?v=dQw4w9WgXcQ"></iframe>'
    cleaned = sanitize_rich_text(watch, youtube)
    assert 'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"' in cleaned
    assert 'sandbox="' in cleaned
    assert 'loading="lazy"' in cleaned
    other = '<iframe src="https://vimeo.com/123456789"></iframe>'
    assert "<iframe" not in sanitize_rich_text(other, youtube)
    expected = 'src="https://player.vimeo.com/video/123456789?dnt=1"'
    assert expected in sanitize_rich_text(other, vimeo)
    assert "<iframe" not in sanitize_rich_text(watch, off)


def test_classes_are_limited_to_the_enabled_curated_names() -> None:
    """Only palette classes of enabled features keep their class attribute."""
    colors = ProfileSpec(key="t", features=frozenset({"colors"}))
    fonts = ProfileSpec(key="t", features=frozenset({"fonts"}))
    html = '<p>a <span class="mf-fg-red evil mf-bg-blue">x</span></p>'
    expected = '<p>a <span class="mf-fg-red mf-bg-blue">x</span></p>'
    assert sanitize_rich_text(html, colors) == expected
    assert "class=" not in sanitize_rich_text(html, BARE)
    assert "mf-fg-red" not in sanitize_rich_text(html, fonts)


def test_blank_editor_output_collapses_to_empty() -> None:
    """Empty editor markup is stored as an empty string."""
    assert sanitize_rich_text("<p><br></p>", FULL) == ""
    assert sanitize_rich_text("<p>&nbsp;</p>", FULL) == ""
    assert sanitize_rich_text(None, FULL) == ""
    assert sanitize_rich_text('<img src="/media/x.png">', FULL) != ""


def test_text_length_counts_visible_text_only() -> None:
    """Length limits apply to text, not markup."""
    assert text_length("<p>ab<strong>cd</strong></p><script>zzzz</script>") == 4
    assert text_length("<p>&lt;b&gt;</p>") == 3


def test_render_sanitises_again_and_formats_legacy_plain_text() -> None:
    """Rendering re-sanitises stored HTML and paragraphs legacy plain text."""
    hostile = "<p>ok</p><script>alert(1)</script><img src=x onerror=alert(1)>"
    rendered = render_rich_text(hostile)
    assert "<script" not in rendered
    assert "onerror" not in rendered
    legacy = render_rich_text("line one\n\nline 2 < 3 & 4")
    assert "<p>line one</p>" in legacy
    assert "2 &lt; 3 &amp; 4" in legacy


def test_default_profiles_resolve_for_every_context() -> None:
    """Code defaults are restrictive for comments and open for blog posts."""
    comment = default_profile("comment")
    assert not comment.images_enabled
    assert not comment.video_enabled
    assert default_profile("blog_post").upload_available
    unknown = default_profile("something-new")
    assert unknown.key == "something-new"
    assert unknown.features == default_profile("admin_default").features


DATA_URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"


def test_pasted_data_uri_images_never_reach_stored_html() -> None:
    """A pasted ``data:`` image is dropped with its inline styling, text is kept."""
    pasted = (
        f'<p>Before</p><img src="{DATA_URI}" width="4000" height="3000" '
        'style="width:4000px;border:2px solid red"><p>After</p>'
    )

    cleaned = sanitize_rich_text(pasted, FULL)

    assert cleaned == "<p>Before</p><p>After</p>"
    for forbidden in ("data:", "style", "4000"):
        assert forbidden not in cleaned
    assert sanitize_rich_text(f'<p><img src="{DATA_URI}"></p>', FULL) == ""


def test_images_always_carry_an_alt_attribute() -> None:
    """A missing ``alt`` becomes an empty one; a real description is kept as is."""
    assert (
        sanitize_rich_text('<p><img src="/media/editor/a.png"></p>', FULL)
        == '<p><img src="/media/editor/a.png" alt=""></p>'
    )
    described = '<p><img src="/media/editor/a.png" alt="Fastboot screen"></p>'
    assert sanitize_rich_text(described, FULL) == described


def test_figures_with_captions_survive_and_sizes_stay_numeric() -> None:
    """Uploaded images become figures; only numeric intrinsic sizes are kept."""
    html = (
        '<figure><img src="/media/editor/a.png" alt="" width="1200" height="800">'
        "<figcaption>Download mode</figcaption></figure>"
    )

    assert sanitize_rich_text(html, FULL) == html
    hostile = (
        '<figure style="width:9999px"><img src="/media/editor/a.png" alt="x" '
        'width="100%" height="auto" class="huge" style="float:left"></figure>'
    )
    cleaned = sanitize_rich_text(hostile, FULL)
    assert cleaned == '<figure><img src="/media/editor/a.png" alt="x"></figure>'
    assert sanitize_rich_text(cleaned, FULL) == cleaned


def test_image_completion_is_idempotent_and_rendered_pages_use_it() -> None:
    """Stored and re-rendered content agree, so saving twice changes nothing."""
    once = sanitize_rich_text('<img src="/media/editor/a.png">text', FULL)

    assert sanitize_rich_text(once, FULL) == once
    assert 'alt=""' in render_rich_text('<img src="/media/editor/a.png">')


def test_remote_and_blob_images_are_rejected_not_embedded() -> None:
    """Images the site does not host cannot be embedded (strict ``img-src``)."""
    for src in (
        "https://cdn.example/a.png",
        "http://cdn.example/a.png",
        "blob:https://app.example/1234",
        "file:///C:/a.png",
        "//cdn.example/a.png",
    ):
        cleaned = sanitize_rich_text(f'<p>x</p><img src="{src}" alt="a">', FULL)
        assert cleaned == "<p>x</p>", src
