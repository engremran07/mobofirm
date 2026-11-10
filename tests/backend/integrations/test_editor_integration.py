"""Rich-text editor integration across blog, comments, pages, marketplace and admin."""

from __future__ import annotations

from typing import ClassVar

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory

from apps.blog.forms import PostForm
from apps.blog.models import Post
from apps.blog.rendering import render_article
from apps.comments.services.comment_service import clean_body, submit_comment
from apps.core.editor.csp import CSP_ATTRIBUTE, video_embed_csp_middleware
from apps.core.editor.forms import RichTextFormMixin
from apps.core.editor.video import VIDEO_FRAME_HOSTS
from apps.marketplace.forms import ListingForm
from apps.pages.rendering import render_page_content
from apps.site_settings.editor_models import EditorProfile
from apps.site_settings.editor_store import clear_profile_cache

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("seeded_profiles")]

VIDEO = '<iframe src="https://www.youtube.com/watch?v=dQw4w9WgXcQ"></iframe>'
EMBED = "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
HOSTILE = (
    '<script>alert(1)</script><img src=x onerror=alert(1)><a href="javascript:1">x</a>'
)


def _author():
    return get_user_model().objects.create_user(
        username="ed-author", email="ed-author@example.org"
    )


def test_post_form_sanitises_with_the_blog_profile_and_keeps_video() -> None:
    """The blog form keeps video embeds (rewritten) and drops hostile markup."""
    form = PostForm(
        {"title": "Guide", "body": f"<p>Hi</p>{VIDEO}{HOSTILE}", "access": "free"},
        user=_author(),
    )
    form.is_valid()
    body = form.cleaned_data["body"]
    assert EMBED in body
    assert "<script" not in body
    assert "onerror" not in body
    assert "javascript:" not in body


def test_post_form_follows_profile_changes_but_stored_posts_keep_their_video() -> None:
    """Disabling video narrows new input only; saved posts still render it."""
    author = _author()
    post = Post.objects.create(title="Video", body=f"<p>x</p>{VIDEO}", author=author)
    assert EMBED in post.body
    assert EMBED in render_article(post.body).html
    EditorProfile.objects.filter(context="blog_post").update(video_providers=[])
    clear_profile_cache()
    form = PostForm(
        {"title": "New", "body": f"<p>Longer text</p>{VIDEO}", "access": "free"},
        user=author,
    )
    assert form.is_valid(), form.errors
    post.save()
    assert EMBED in post.body


def test_post_form_uses_the_editor_widget() -> None:
    """The article body is rendered as an editor textarea with the profile."""
    html = str(PostForm(user=_author())["body"])
    assert 'data-mf-editor="1"' in html
    assert "blog_post" in html


def test_comment_body_is_sanitised_per_comment_profile() -> None:
    """Comments keep basic formatting but never images, video or scripts."""
    cleaned = clean_body(
        '<p>Nice <strong>guide</strong></p><img src="/media/editor/a.png">'
        f"{VIDEO}{HOSTILE}"
    )
    assert "<strong>guide</strong>" in cleaned
    for forbidden in ("<img", "<iframe", "<script", "onerror", "javascript:"):
        assert forbidden not in cleaned


def test_plain_text_comments_become_paragraphs_and_keep_entities() -> None:
    """Legacy-style plain text is stored as safe HTML paragraphs."""
    assert clean_body("Great article!\n\nThanks & bye < 3") == (
        "<p>Great article!</p>\n\n<p>Thanks &amp; bye &lt; 3</p>"
    )


def test_comment_length_limit_uses_visible_text() -> None:
    """Markup does not count towards the 5000 character limit."""
    assert clean_body("<p>" + "a" * 4000 + "</p>")
    with pytest.raises(ValidationError):
        clean_body("<p>" + "a" * 5001 + "</p>")
    with pytest.raises(ValidationError):
        clean_body("<p><br></p>")


def test_moderation_screens_link_targets_inside_anchors() -> None:
    """A spammy link hidden in an anchor still counts for moderation."""
    user = _author()
    post = Post.objects.create(title="Open", body="<p>x</p>", author=user)
    links = "".join(f'<a href="https://spam{i}.example">link</a> ' for i in range(6))
    result = submit_comment(post, user, f"<p>Buy now! {links}</p>")
    assert result.comment.status != "approved"
    assert "<a " in result.comment.body
    assert 'rel="nofollow noopener"' in result.comment.body


def test_comment_templates_use_the_editor_and_render_rich_text() -> None:
    """The comment form carries the editor hooks; nodes render sanitised HTML."""
    user = _author()
    post = Post.objects.create(title="Talk", body="<p>x</p>", author=user)
    request = RequestFactory().get("/")
    request.user = user
    html = render_to_string(
        "comments/form.html",
        {"post": post, "form_id": "f1", "parent_id": "", "user": user},
        request=request,
    )
    assert 'data-mf-editor="1"' in html
    assert 'name="body"' in html
    assert "build/editor.js" in html


def test_listing_form_sanitises_the_description() -> None:
    """Marketplace descriptions use the listing profile."""
    form = ListingForm(
        {"title": "Tool", "description": f"<p>ok</p>{HOSTILE}", "price": "5.00"}
    )
    form.is_valid()
    assert "<script" not in form.cleaned_data["description"]
    assert 'data-mf-editor="1"' in str(form["description"])


def test_pages_keep_audited_video_embeds_and_drop_the_rest() -> None:
    """CMS pages render allowed video embeds only."""
    html = render_page_content(
        f'<p>Intro</p>{VIDEO}<iframe src="https://evil.example/x"></iframe>', "html"
    )
    assert EMBED in html
    assert "evil.example" not in html
    assert html.count("<iframe") == 1


def test_form_mixin_conditions_apply_only_when_the_other_field_matches() -> None:
    """``rich_text_when`` limits the editor and sanitising to one format."""

    class PageLike(RichTextFormMixin, forms.Form):
        """Form whose ``content`` is rich text only for HTML."""

        rich_text_fields: ClassVar[dict[str, str]] = {"content": "page"}
        rich_text_when: ClassVar[dict[str, tuple[str, str]]] = {
            "content": ("format", "html")
        }
        format = forms.ChoiceField(choices=[("md", "Markdown"), ("html", "HTML")])
        content = forms.CharField(widget=forms.Textarea)

    markdown = PageLike({"format": "md", "content": "<script>x</script> *hi*"})
    assert markdown.is_valid()
    assert markdown.cleaned_data["content"] == "<script>x</script> *hi*"
    html = PageLike({"format": "html", "content": "<script>x</script><p>hi</p>"})
    assert html.is_valid()
    assert html.cleaned_data["content"] == "<p>hi</p>"
    assert 'data-mf-editor-when="format=html"' in str(html["content"])


def _csp(response: HttpResponse) -> dict[str, list[str]]:
    middleware = video_embed_csp_middleware(lambda _request: response)
    middleware(HttpRequest())
    return getattr(response, CSP_ATTRIBUTE, {})


def test_csp_allows_video_players_only_on_pages_that_need_them() -> None:
    """``frame-src`` stays ``'none'`` unless an embed or an editor is present."""
    plain = HttpResponse("<p>hello</p>", content_type="text/html")
    assert not _csp(plain)
    embed = HttpResponse(f'<iframe class="mf-video" src="{EMBED}"></iframe>')
    policy = _csp(embed)
    assert policy["frame-src"] == list(VIDEO_FRAME_HOSTS)
    assert "'none'" not in policy["frame-src"]
    assert policy["default-src"] == ["'self'"]
    editor = HttpResponse(
        '<textarea data-mf-editor-config="'
        '{&quot;videoProviders&quot;:[&quot;youtube&quot;]}">'
    )
    assert _csp(editor)["frame-src"] == list(VIDEO_FRAME_HOSTS)
    no_video = HttpResponse(
        '<textarea data-mf-editor-config="{&quot;videoProviders&quot;:[]}"></textarea>'
    )
    assert not _csp(no_video)


def test_csp_merges_into_an_existing_view_policy() -> None:
    """The admin policy attached earlier keeps its directives."""
    response = HttpResponse(f'<iframe class="mf-video" src="{EMBED}"></iframe>')
    setattr(
        response,
        CSP_ATTRIBUTE,
        {"script-src": ["'self'", "'unsafe-eval'"], "frame-src": ["'none'"]},
    )
    policy = _csp(response)
    assert policy["script-src"] == ["'self'", "'unsafe-eval'"]
    assert policy["frame-src"] == list(VIDEO_FRAME_HOSTS)
