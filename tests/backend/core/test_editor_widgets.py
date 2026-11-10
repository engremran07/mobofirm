"""Widget rendering, the form mixin/field and the disabled fallback."""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

import pytest
from django import forms
from django.template import Context, Template

from apps.core.editor import sanitize_rich_text, sanitize_stored_html
from apps.core.editor.forms import RichTextField, RichTextFormMixin
from apps.core.editor.sanitizer import render_rich_text
from apps.core.editor.widgets import RichTextWidget
from apps.site_settings.editor_models import EditorProfile
from apps.site_settings.editor_store import clear_profile_cache

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("seeded_profiles")]


class NoteForm(RichTextFormMixin, forms.Form):
    """A plain form whose ``body`` is upgraded by the mixin."""

    rich_text_fields: ClassVar[dict[str, str]] = {"body": "comment"}
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "class": "x"}))


class ArticleForm(forms.Form):
    """A form using the dedicated field class."""

    body = RichTextField(profile="blog_post", required=False)


def _config(markup: str) -> dict[str, Any]:
    match = re.search(r"data-mf-editor-config=\"([^\"]*)\"", markup)
    assert match, markup
    return json.loads(match.group(1).replace("&quot;", '"'))


def test_widget_renders_config_and_one_lazy_loader_script() -> None:
    """The textarea carries the profile config; the script is a module tag."""
    markup = RichTextWidget("blog_post").render("body", "<p>x</p>", {"id": "id_body"})
    config = _config(markup)
    assert config["profile"] == "blog_post"
    assert "tables" in config["features"]
    assert config["upload"]["enabled"] is True
    assert 'data-mf-editor="1"' in markup
    assert markup.count('<script type="module"') == 1
    assert "build/editor.js" in markup
    assert "style=" not in markup
    assert "onclick" not in markup


def test_widget_reflects_disabled_features_and_upload() -> None:
    """Reduced profiles expose neither images nor upload."""
    config = _config(RichTextWidget("comment").render("body", ""))
    assert "images" not in config["features"]
    assert config["upload"]["enabled"] is False
    assert config["videoProviders"] == []


def test_master_switch_off_gives_a_plain_textarea() -> None:
    """A disabled profile renders a bare textarea without script or hooks."""
    EditorProfile.objects.filter(context="comment").update(enabled=False)
    markup = RichTextWidget("comment").render("body", "hello")
    assert markup.startswith("<textarea")
    assert "data-mf-editor" not in markup
    assert "<script" not in markup
    assert ">\nhello</textarea>" in markup


def test_mixin_swaps_the_widget_and_keeps_attributes() -> None:
    """The mixin replaces only the widget, preserving the original attrs."""
    form = NoteForm()
    widget = form.fields["body"].widget
    assert isinstance(widget, RichTextWidget)
    assert widget.attrs["class"] == "x"
    assert widget.is_required is True


def test_mixin_sanitises_and_enforces_required_and_length() -> None:
    """Submitted HTML is cleaned per profile; blank and over-long values fail."""
    ok = NoteForm({"body": '<p>hi <script>x</script><a href="javascript:1">l</a></p>'})
    assert ok.is_valid(), ok.errors
    assert "<script" not in ok.cleaned_data["body"]
    assert "javascript" not in ok.cleaned_data["body"]
    blank = NoteForm({"body": "<p><br></p>"})
    assert not blank.is_valid()
    assert "body" in blank.errors
    long = NoteForm({"body": "<p>" + "a" * 5001 + "</p>"})
    assert not long.is_valid()
    assert "at most 5000" in str(long.errors["body"])


def test_rich_text_field_cleans_and_allows_blank() -> None:
    """The field class sanitises and collapses empty editor markup."""
    form = ArticleForm({"body": "<p><br></p>"})
    assert form.is_valid()
    assert form.cleaned_data["body"] == ""
    form = ArticleForm(
        {"body": '<p onclick="1">a</p><iframe src="https://x.y"></iframe>'}
    )
    assert form.is_valid()
    assert form.cleaned_data["body"] == "<p>a</p>"


def test_template_filter_resanitises_and_keeps_stored_content_valid() -> None:
    """``rich_text`` strips hostile markup but keeps what an old profile allowed."""
    template = Template("{% load editor_tags %}{{ html|rich_text }}")
    html = (
        '<p>ok <strong>b</strong></p><img src="/media/editor/a.png" onerror="x()">'
        "<script>alert(1)</script><table><tbody><tr><td>c</td></tr></tbody></table>"
    )
    out = template.render(Context({"html": html}))
    assert "<strong>b</strong>" in out
    assert '<img src="/media/editor/a.png">' in out
    assert "<table>" in out
    assert "<script" not in out
    assert "onerror" not in out


def test_content_written_before_a_feature_was_disabled_still_renders() -> None:
    """Disabling Tables narrows new input only; stored tables keep rendering."""
    EditorProfile.objects.filter(context="blog_post").update(features=["bold"])
    clear_profile_cache()
    table = "<table><tbody><tr><td>c</td></tr></tbody></table>"
    assert "<table" not in sanitize_rich_text(table, "blog_post")
    assert "<table" in render_rich_text(table)
    assert "<table" in sanitize_stored_html(table)
