from __future__ import annotations

from typing import Any, ClassVar

from django import forms

from apps.tags.selectors import public_tags

from .models import Category, Post


class SearchForm(forms.Form):
    q = forms.CharField(
        label="Search articles",
        max_length=200,
        widget=forms.TextInput(
            attrs={"placeholder": "Search articles", "class": "form-control"}
        ),
    )


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ("name", "slug", "parent")
        widgets: ClassVar[dict[str, Any]] = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "slug": forms.TextInput(attrs={"class": "form-control"}),
            "parent": forms.Select(attrs={"class": "form-select"}),
        }


class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = (
            "title",
            "summary",
            "body",
            "category",
            "tags",
            "allow_comments",
            "noindex",
            "seo_title",
            "seo_description",
            "canonical_url",
            "hero_image",
        )
        widgets: ClassVar[dict[str, Any]] = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "summary": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "body": forms.Textarea(attrs={"rows": 15, "class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "tags": forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
            "allow_comments": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "noindex": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "seo_title": forms.TextInput(attrs={"class": "form-control"}),
            "seo_description": forms.Textarea(
                attrs={"rows": 2, "class": "form-control"}
            ),
            "canonical_url": forms.URLInput(attrs={"class": "form-control"}),
            "hero_image": forms.URLInput(attrs={"class": "form-control"}),
        }
        help_texts: ClassVar[dict[str, str]] = {
            "hero_image": "Use a path on this site such as /media/cover.jpg; other hosts are blocked by the site's content security policy.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = public_tags()
        if self.instance.pk:
            allowed = allowed | self.instance.tags.all()
        tags_field = self.fields["tags"]
        assert isinstance(tags_field, forms.ModelMultipleChoiceField)
        tags_field.queryset = allowed.distinct().order_by("name")
