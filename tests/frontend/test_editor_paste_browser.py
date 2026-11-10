"""Pasted images in the rich-text editor.

``test_paste_logic_*`` run the real paste module (``frontend/src/modules``) in Chromium
straight from source, so they need no Vite build. ``test_pasted_images_*`` drive the
built editor bundle end to end (paste -> upload endpoint -> figure -> saved post ->
public page) and therefore need ``vite build`` to have been run after editor changes.
"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from PIL import Image
from playwright.sync_api import Page, expect

from apps.site_settings.editor_models import EditorProfile
from tests.frontend.browser_support import login_cookie, overflow, watch
from tests.frontend.catalog_browser_support import locked_chromium

MODULES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "modules"
SOURCE_PREFIX = "**/__frontend_src__/*.js"


def _png(width: int, height: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (198, 47, 47)).save(buffer, "PNG")
    return buffer.getvalue()


def _data_uri(width: int = 8, height: int = 6) -> str:
    return "data:image/png;base64," + base64.b64encode(_png(width, height)).decode()


def _serve_sources(page: Page) -> None:
    """Serve the ES modules of ``frontend/src/modules`` under a fake same-origin path."""

    def handler(route) -> None:
        name = route.request.url.rsplit("/", 1)[-1]
        source = MODULES / name
        if source.parent != MODULES or not source.is_file():
            route.fulfill(status=404, body="")
            return
        route.fulfill(
            status=200,
            content_type="text/javascript",
            body=source.read_text(encoding="utf-8"),
        )

    page.route(SOURCE_PREFIX, handler)


HARNESS = """
async ({ scenarios }) => {
  const { attachPasteHandler, imageAlt, fileFromDataUri, TOO_LARGE } =
    await import('/__frontend_src__/editor-paste.js');
  const out = {};
  for (const scenario of scenarios) {
    const area = document.createElement('div');
    area.contentEditable = 'true';
    document.body.append(area);
    const calls = { images: [], html: [], text: [], say: [] };
    attachPasteHandler(area, {
      config: scenario.config,
      say: (message, isError) => calls.say.push([message, isError]),
      addImage: async (file) => { calls.images.push([file.name, file.type, file.size]); },
      insertHtml: (html) => calls.html.push(html),
      insertText: (text) => calls.text.push(text),
    });
    const transfer = new DataTransfer();
    if (scenario.html) transfer.setData('text/html', scenario.html);
    if (scenario.text) transfer.setData('text/plain', scenario.text);
    for (const file of scenario.files || []) {
      transfer.items.add(new File([new Uint8Array(file.size)], file.name, { type: file.type }));
    }
    const event = new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true });
    area.dispatchEvent(event);
    await new Promise((resolve) => setTimeout(resolve, 60));
    out[scenario.name] = { prevented: event.defaultPrevented, ...calls };
    area.remove();
  }
  out.alt = [
    imageAlt('image.png'), imageAlt('Screenshot 12.png'), imageAlt('fastboot-screen_01.PNG'),
    imageAlt('12345.jpg'), imageAlt(''), imageAlt('x'.repeat(300) + '.png').length,
  ];
  out.fileFromDataUri = [
    fileFromDataUri('https://x.example/a.png', 0, 1000) === null,
    fileFromDataUri('data:image/svg+xml;base64,PHN2Zz4=', 0, 1000) === null,
    fileFromDataUri('data:image/png;base64,@@@', 0, 1000) === null,
    fileFromDataUri('data:image/png;base64,' + 'A'.repeat(4000), 0, 1000) === TOO_LARGE,
  ];
  return out;
}
"""

NORMALIZE = """
async () => {
  const { normalizeHtml } = await import('/__frontend_src__/editor-normalize.js');
  const withImages = { features: ['images', 'colors'] };
  return {
    lone: normalizeHtml('<p><img src="/media/editor/a.png" width="1200" height="800" style="width:900px" class="x" title="t" srcset="a 2x"><br></p>', withImages),
    inline: normalizeHtml('<p>See <img src="/media/editor/a.png" alt="icon"> here</p>', withImages),
    linked: normalizeHtml('<p><a href="https://x.example"><img src="/media/editor/a.png" alt="x"></a></p>', withImages),
    data: normalizeHtml('<p>Hi</p><p><img src="data:image/png;base64,AAAA"></p>', withImages),
    remote: normalizeHtml('<p>Hi</p><img src="https://cdn.example/a.png">', withImages),
    off: normalizeHtml('<p>Hi</p><p><img src="/media/editor/a.png"></p>', { features: [] }),
    caption: normalizeHtml('<figure><img src="/media/editor/a.png" alt="a" width="100%"><figcaption>Download mode</figcaption></figure>', withImages),
    emptyCaption: normalizeHtml('<figure><img src="/media/editor/a.png" alt="a"><figcaption><br></figcaption></figure>', withImages),
    orphan: normalizeHtml('<figure><figcaption>no picture</figcaption></figure><p>Text</p>', withImages),
    emptyParagraph: normalizeHtml('<p></p><p>Text</p>', withImages),
    nothing: normalizeHtml('<p><img src="data:image/png;base64,AAAA"></p>', withImages),
  };
}
"""

UPLOADS = {
    "enabled": True,
    "url": "/site/editor/upload/",
    "maxKb": 512,
}
IMAGES_ON = {"features": ["images", "colors"], "upload": UPLOADS}


def _scenarios() -> list[dict[str, Any]]:
    uri = _data_uri()
    return [
        {
            "name": "data_uri_with_styles",
            "config": IMAGES_ON,
            "html": (
                f'<p style="color:#c62f2f">Hi</p><img src="{uri}" width="4000" '
                'height="3000" style="width:4000px;border:2px solid red">'
            ),
        },
        {
            "name": "two_data_uris",
            "config": IMAGES_ON,
            "html": f'<img src="{uri}"><img src="{uri}">',
        },
        {
            "name": "file_wins_over_remote_markup",
            "config": IMAGES_ON,
            "html": '<img src="https://cdn.example/a.png">',
            "files": [{"name": "image.png", "type": "image/png", "size": 10}],
        },
        {
            "name": "remote_only",
            "config": IMAGES_ON,
            "html": '<p>Caption text</p><img src="https://cdn.example/a.png">',
        },
        {
            "name": "blob_only",
            "config": IMAGES_ON,
            "html": '<img src="blob:https://docs.example/1234">',
        },
        {
            "name": "uploads_disabled",
            "config": {"features": ["images"], "upload": {"enabled": False}},
            "files": [{"name": "image.png", "type": "image/png", "size": 10}],
        },
        {
            "name": "images_not_allowed",
            "config": {"features": ["bold"], "upload": UPLOADS},
            "html": f'<p>Hi</p><img src="{uri}">',
        },
        {
            "name": "too_large",
            "config": {**IMAGES_ON, "upload": {**UPLOADS, "maxKb": 1}},
            "html": '<img src="data:image/png;base64,' + "A" * 4000 + '">',
        },
        {
            "name": "styled_text_only",
            "config": {"features": ["colors"], "upload": UPLOADS},
            "html": '<p style="color:#c62f2f;font-family:Calibri">Styled</p>',
        },
        {"name": "plain_text", "config": IMAGES_ON, "html": "<p>Just text</p>", "text": "Just text"},
    ]


@pytest.mark.django_db(transaction=True)
def test_paste_logic_uploads_images_and_explains_what_it_rejects(live_server) -> None:
    failures: list[str] = []
    with locked_chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        _serve_sources(page)
        page.goto(f"{live_server.url}/", wait_until="load")
        result = page.evaluate(HARNESS, {"scenarios": _scenarios()})

    data = result["data_uri_with_styles"]
    assert data["prevented"], "the editor must take over pastes that carry images"
    assert [(name, kind) for name, kind, _ in data["images"]] == [
        ("pasted-image-1.png", "image/png")
    ]
    assert len(data["html"]) == 1
    cleaned = data["html"][0]
    assert "Hi" in cleaned
    for forbidden in ("style=", "<img", "data:", "4000"):
        assert forbidden not in cleaned, forbidden
    assert data["say"] == []

    assert [row[0] for row in result["two_data_uris"]["images"]] == [
        "pasted-image-1.png",
        "pasted-image-2.png",
    ]

    winner = result["file_wins_over_remote_markup"]
    assert winner["prevented"]
    assert [row[0] for row in winner["images"]] == ["image.png"]
    assert winner["html"] == [] and winner["text"] == [] and winner["say"] == []

    for name in ("remote_only", "blob_only"):
        case = result[name]
        assert case["prevented"] and case["images"] == [], name
        message, is_error = case["say"][-1]
        assert is_error and "hosted elsewhere" in message, name
        assert "image button" in message
    assert "Caption text" in result["remote_only"]["html"][0]
    assert "<img" not in result["remote_only"]["html"][0]

    disabled = result["uploads_disabled"]
    assert disabled["images"] == []
    assert disabled["say"] == [
        ["Image upload is not available here, so the pasted image was not added.", True]
    ]

    denied = result["images_not_allowed"]
    assert denied["images"] == [] and "Hi" in denied["html"][0]
    assert denied["say"][0][0].startswith("Images are not allowed in this field")

    large = result["too_large"]
    assert large["images"] == []
    assert "too large" in large["say"][-1][0] and large["say"][-1][1] is True

    styled = result["styled_text_only"]
    assert styled["prevented"] and styled["images"] == []
    assert styled["html"] and "style=" not in styled["html"][0]
    assert "Calibri" not in styled["html"][0]

    plain = result["plain_text"]
    assert not plain["prevented"], "ordinary text pastes keep the native behaviour"
    assert plain["html"] == plain["images"] == plain["say"] == []

    assert result["alt"] == ["", "", "fastboot screen 01", "", "", 125]
    assert result["fileFromDataUri"] == [True, True, True, True]
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_paste_logic_normalises_images_to_responsive_figures(live_server) -> None:
    with locked_chromium() as browser:
        page = browser.new_page()
        _serve_sources(page)
        page.goto(f"{live_server.url}/", wait_until="load")
        out = page.evaluate(NORMALIZE)

    lone = out["lone"]
    assert lone.startswith("<figure><img ") and lone.endswith("<figcaption></figcaption></figure>")
    assert 'src="/media/editor/a.png"' in lone
    assert 'width="1200"' in lone and 'height="800"' in lone and 'alt=""' in lone
    for attribute in ("style", "class", "title", "srcset"):
        assert attribute not in lone, attribute
    assert out["inline"] == '<p>See <img src="/media/editor/a.png" alt="icon"> here</p>'
    assert out["linked"].startswith("<p><a "), "a linked image keeps its link"
    assert out["data"] == "<p>Hi</p>" and out["remote"] == "<p>Hi</p>"
    assert out["off"] == "<p>Hi</p>"
    assert out["caption"] == (
        '<figure><img src="/media/editor/a.png" alt="a">'
        "<figcaption>Download mode</figcaption></figure>"
    )
    assert out["emptyCaption"].endswith("<figcaption></figcaption></figure>")
    assert out["orphan"] == "<p>Text</p>"
    assert out["emptyParagraph"] == "<p>Text</p>"
    assert out["nothing"] == ""


# -- the built editor, end to end (needs ``vite build`` after editor changes) -----------


def _write_clipboard(page: Page, items: dict[str, tuple[str, str]]) -> None:
    """Put ``{mime: (encoding, payload)}`` on the system clipboard (base64 or text)."""
    page.evaluate(
        """async (items) => {
            const blobs = {};
            for (const [mime, [encoding, payload]] of Object.entries(items)) {
                const bytes = encoding === 'base64'
                    ? Uint8Array.from(atob(payload), (c) => c.charCodeAt(0))
                    : new TextEncoder().encode(payload);
                blobs[mime] = new Blob([bytes], { type: mime });
            }
            await navigator.clipboard.write([new ClipboardItem(blobs)]);
        }""",
        items,
    )


def _editor(page: Page):
    page.locator("textarea[data-mf-editor]").first.evaluate("e => e.scrollIntoView()")
    area = page.locator(".note-editor .note-editable").first
    area.wait_for(state="visible", timeout=20_000)
    area.click()
    return area


def _body_value(page: Page) -> str:
    return page.evaluate("() => document.querySelector('textarea[name=body]').value")


@pytest.mark.django_db(transaction=True)
def test_pasted_images_are_uploaded_sanitised_and_render_responsively(
    live_server, settings, tmp_path
) -> None:
    settings.MEDIA_ROOT = tmp_path
    EditorProfile.seed()
    user = get_user_model().objects.create_superuser(
        username="paste-admin",
        email="paste-admin@example.test",
        email_verified_at=timezone.now(),
    )
    cookies = login_cookie(user)
    big = base64.b64encode(_png(2400, 1600)).decode()
    small = base64.b64encode(_png(40, 30)).decode()
    failures: list[str] = []

    with locked_chromium() as browser:
        context = browser.new_context(viewport={"width": 1100, "height": 900})
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        context.add_cookies(
            [
                {"name": name, "value": value, "url": live_server.url}
                for name, value in cookies.items()
            ]
        )
        # The test server does not serve MEDIA_ROOT: hand the stored files to the page.
        context.route(
            "**/media/editor/**",
            lambda route: route.fulfill(
                status=200,
                content_type="image/png",
                body=next(tmp_path.rglob(route.request.url.rsplit("/", 1)[-1])).read_bytes(),
            ),
        )
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}/blog/create/", wait_until="load")
        area = _editor(page)
        message = page.locator(".mf-editor-message")

        # 1. An image copied from a screenshot tool: a file on the clipboard.
        _write_clipboard(page, {"image/png": ("base64", big)})
        page.keyboard.press("Control+V")
        expect(area.locator("figure img[src^='/media/editor/']")).to_have_count(1, timeout=20_000)
        expect(message).to_contain_text("Image added")
        image = area.locator("figure img").first
        assert image.get_attribute("width") == "2000"  # re-encoded within the size limit
        assert image.get_attribute("alt") == ""
        assert image.get_attribute("style") in (None, "")
        assert area.locator("figure figcaption").count() == 1
        assert area.locator("p figure").count() == 0, "a figure must not nest in a <p>"

        # 2. Markup with a data: image, a fixed size and inline styles (Word, web pages).
        html = (
            "<p>Pasted caption</p>"
            f'<img src="data:image/png;base64,{small}" width="4000" height="3000" '
            'style="width:4000px;border:2px solid red">'
        )
        _write_clipboard(page, {"text/html": ("text", html)})
        area.click()
        page.keyboard.press("Control+End")
        page.keyboard.press("Control+V")
        expect(area.locator("figure img[src^='/media/editor/']")).to_have_count(2, timeout=20_000)
        assert "data:" not in area.inner_html()
        assert "4000" not in area.inner_html()

        # 3. An image hosted on another site cannot be embedded: say so, insert nothing.
        _write_clipboard(
            page, {"text/html": ("text", '<img src="https://cdn.example/a.png">')}
        )
        page.keyboard.press("Control+V")
        expect(message).to_contain_text("hosted elsewhere")
        assert area.locator("img[src^='https:']").count() == 0

        # 4. What the server will store: figures with real files, no data URI, no style.
        stored = _body_value(page)
        assert stored.count("<figure>") == 2
        assert "data:" not in stored and "style=" not in stored
        assert stored.count('src="/media/editor/') == 2

        # 5. Save, then check the article exactly as readers get it.
        page.fill("#id_title", "Pasted image article")
        page.fill("#id_summary", "An article whose images were pasted into the editor.")
        page.get_by_role("button", name="Save draft").click()
        page.wait_for_url("**/blog/pasted-image-article/", timeout=20_000)
        body = page.locator(".article-body")
        expect(body.locator("figure img")).to_have_count(2)
        for width in (360, 768, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            page.reload(wait_until="load")
            sizes = overflow(page)
            assert sizes["document"] <= sizes["viewport"] + 1, (width, sizes)
            for index in range(2):
                box = body.locator("figure img").nth(index).bounding_box()
                container = body.bounding_box()
                assert box and container
                assert box["width"] <= container["width"] + 1, (width, box, container)
            style = body.locator("figure img").first.evaluate(
                "e => { const s = getComputedStyle(e); return [s.maxWidth, s.height, e.naturalHeight / e.naturalWidth]; }"
            )
            assert style[0] == "100%"
        empty_caption = body.locator("figure figcaption").first
        assert empty_caption.is_hidden(), "an empty caption must not take up space"

    assert not failures, failures
