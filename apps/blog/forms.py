from __future__ import annotations

from django import forms

from .models import Category, Post


class SearchForm(forms.Form):
    q = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={"placeholder": "Search blog…"}),
    )


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "slug", "parent"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus:border-primary focus:ring-2 focus:ring-primary/20"
                }
            ),
            "slug": forms.TextInput(
                attrs={
                    "class": "w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus:border-primary focus:ring-2 focus:ring-primary/20"
                }
            ),
            "parent": forms.Select(
                attrs={
                    "class": "w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus:border-primary focus:ring-2 focus:ring-primary/20"
                }
            ),
        }


class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = [
            "title",
            "summary",
            "body",
            "category",
            "tags",
            "status",
            "publish_at",
            "featured",
            "allow_comments",
            "noindex",
            "seo_title",
            "seo_description",
            "canonical_url",
            "hero_image",
        ]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 15, "class": "form-control"}),
            "summary": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "publish_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "form-control"}
            ),
            "seo_description": forms.Textarea(
                attrs={"rows": 2, "class": "form-control"}
            ),
        }
