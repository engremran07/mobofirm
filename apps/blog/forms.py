"""Forms for blog search, contributions, update proposals, and staff editing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from django import forms
from django.urls import reverse

from apps.core.editor import text_length
from apps.core.editor.forms import RichTextFormMixin
from apps.tags.selectors import public_tags

from .access import can_set_premium_access, is_approved_seller
from .models import Category, Post, PostAccess


class SearchForm(forms.Form):
    """Search published blog content."""

    q = forms.CharField(
        label="Search articles",
        max_length=200,
        widget=forms.TextInput(
            attrs={"placeholder": "Search articles", "class": "form-control"}
        ),
    )


class CategoryForm(forms.ModelForm):
    """Create or edit a blog category."""

    class Meta:
        model = Category
        fields = ("name", "slug", "parent")
        widgets: ClassVar[dict[str, Any]] = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "slug": forms.TextInput(attrs={"class": "form-control"}),
            "parent": forms.Select(attrs={"class": "form-select"}),
        }


class ContributionForm(RichTextFormMixin, forms.ModelForm):
    """A member's blog contribution: the article and who may read it.

    Free and Featured are open to every contributor. Paid is offered only to approved
    marketplace sellers, who must also propose a price; a forged Paid value from anyone
    else is not a valid choice and is rejected. SEO, comments and hero image stay with
    editors, and reviewers confirm (or lower) the access type.
    """

    rich_text_fields: ClassVar[Mapping[str, str]] = {"body": "blog_contribution"}
    MIN_BODY_CHARS: ClassVar[int] = 40

    class Meta:
        model = Post
        fields = (
            "title",
            "summary",
            "body",
            "category",
            "tags",
            "access",
            "proposed_price",
        )
        widgets: ClassVar[dict[str, Any]] = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "summary": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "body": forms.Textarea(attrs={"rows": 15, "class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "tags": forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
            "access": forms.Select(attrs={"class": "form-select"}),
            "proposed_price": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0.01"}
            ),
        }
        labels: ClassVar[dict[str, str]] = {
            "access": "Access",
            "proposed_price": "Proposed price (USD)",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "summary": "One or two sentences shown in lists. Optional.",
            "category": "Pick the closest topic.",
            "tags": "Pick existing tags that fit; reviewers can add more.",
        }

    def __init__(self, *args, user=None, check_title_matches: bool = False, **kwargs):
        """Configure contribution fields for the submitting member."""
        super().__init__(*args, **kwargs)
        if check_title_matches:
            self.fields["title"].widget.attrs.update(
                {
                    "hx-get": reverse("blog:contribute_title_matches"),
                    "hx-trigger": "input changed delay:400ms, search",
                    "hx-include": "this",
                    "hx-target": "#contribution-title-matches",
                    "hx-swap": "innerHTML",
                    "hx-sync": "this:replace",
                    "hx-indicator": "#contribution-title-check-indicator",
                    "aria-controls": "contribution-title-matches",
                }
            )
        allowed = public_tags()
        if self.instance.pk:
            allowed = allowed | self.instance.tags.all()
        tags_field = self.fields["tags"]
        assert isinstance(tags_field, forms.ModelMultipleChoiceField)
        tags_field.queryset = allowed.distinct().order_by("name")
        tags_field.required = False
        self.fields["category"].required = True
        category_field = self.fields["category"]
        assert isinstance(category_field, forms.ModelChoiceField)
        category_field.empty_label = "Choose a category"
        self.seller = user is not None and is_approved_seller(user)
        access_field = self.fields["access"]
        assert isinstance(access_field, forms.ChoiceField)
        access_field.choices = [
            (value, label)
            for value, label in PostAccess.choices
            if self.seller or value != PostAccess.PAID
        ]
        access_field.required = False
        access_field.initial = PostAccess.FREE
        if self.seller:
            access_field.help_text = (
                "Free is open to everyone. Featured shows a teaser to readers without "
                "access. Paid needs a proposed price. Reviewers confirm the access "
                "type and the final price."
            )
            self.fields["proposed_price"].required = False
            self.fields[
                "proposed_price"
            ].help_text = "Needed for paid posts. Reviewers set the final price."
        else:
            access_field.help_text = (
                "Free is open to everyone. Featured shows a teaser to readers without "
                "access. Paid is available to approved sellers. Reviewers confirm the "
                "access type."
            )
            del self.fields["proposed_price"]

    def clean_access(self) -> str:
        """Default an omitted access choice to free access."""
        return self.cleaned_data.get("access") or PostAccess.FREE

    def clean(self) -> dict[str, Any]:
        """Enforce paid-post eligibility and the minimum body length."""
        cleaned = super().clean()
        access = cleaned.get("access") or PostAccess.FREE
        price = cleaned.get("proposed_price")
        if access == PostAccess.PAID:
            if not self.seller:
                self.add_error("access", "Only approved sellers can offer paid posts.")
            elif price is None or price <= 0:
                self.add_error(
                    "proposed_price", "A paid post needs a proposed price above zero."
                )
        else:
            cleaned["proposed_price"] = None
        body = cleaned.get("body")
        if (
            body is not None
            and "body" not in self.errors
            and text_length(body) < self.MIN_BODY_CHARS
        ):
            self.add_error(
                "body",
                f"Write at least {self.MIN_BODY_CHARS} characters so reviewers "
                "have something to assess.",
            )
        return cleaned


class _PostUpdateFieldsForm(RichTextFormMixin, forms.Form):
    """Shared article fields for member proposals and staff review."""

    rich_text_fields: ClassVar[Mapping[str, str]] = {"body": "blog_contribution"}

    title = forms.CharField(
        min_length=3,
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    summary = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )
    body = forms.CharField(
        min_length=10,
        max_length=50000,
        widget=forms.Textarea(attrs={"rows": 15, "class": "form-control"}),
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=public_tags(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
    )

    def __init__(
        self, *args, post: Post, initial: dict[str, Any] | None = None, **kwargs
    ):
        """Initialize from a live post while retaining its current tag choices."""
        self.post = post
        values = {
            "title": post.title,
            "summary": post.summary,
            "body": post.body,
            "category": post.category_id,
            "tags": list(post.tags.values_list("pk", flat=True)),
        }
        if initial:
            values.update(initial)
        super().__init__(*args, initial=values, **kwargs)
        tags_field = self.fields["tags"]
        assert isinstance(tags_field, forms.ModelMultipleChoiceField)
        tags_field.queryset = (
            (public_tags() | post.tags.all()).distinct().order_by("name")
        )

    def changed_proposal_fields(self) -> dict[str, Any]:
        """Return only submitted fields that differ from the live post."""
        values = self.cleaned_data
        changes: dict[str, Any] = {}
        for name in ("title", "summary", "body"):
            if values[name] != getattr(self.post, name):
                changes[name] = values[name]
        category = values.get("category")
        category_id = category.pk if category is not None else None
        if category_id != self.post.category_id:
            changes["category_id"] = category_id
        tag_ids = sorted(tag.pk for tag in values["tags"])
        current_tag_ids = sorted(self.post.tags.values_list("pk", flat=True))
        if tag_ids != current_tag_ids:
            changes["tags"] = tag_ids
        return changes


class PostUpdateProposalForm(_PostUpdateFieldsForm):
    """Suggest changes without editing the live post."""


class PostUpdateProposalReviewForm(_PostUpdateFieldsForm):
    """Review or adjust fields included in a member's update proposal."""

    reason = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )

    def __init__(self, *args, proposal, **kwargs):
        """Show only the changed fields from the selected proposal."""
        self.proposal = proposal
        changes = proposal.proposed_changes
        initial = {}
        for key in ("title", "summary", "body"):
            if key in changes:
                initial[key] = changes[key]
        if "category_id" in changes:
            initial["category"] = changes["category_id"]
        if "tags" in changes:
            initial["tags"] = changes["tags"]
        super().__init__(*args, post=proposal.post, initial=initial, **kwargs)
        for field_name in ("title", "summary", "body"):
            if field_name not in changes:
                self.fields.pop(field_name)
        if "category_id" not in changes:
            self.fields.pop("category")
        if "tags" not in changes:
            self.fields.pop("tags")

    def reviewed_changes(self) -> dict[str, Any]:
        """Return reviewed values in the proposal service's field format."""
        values = self.cleaned_data
        changes: dict[str, Any] = {}
        for name in ("title", "summary", "body"):
            if name in values:
                changes[name] = values[name]
        if "category" in values:
            category = values["category"]
            changes["category_id"] = category.pk if category is not None else None
        if "tags" in values:
            changes["tags"] = sorted(tag.pk for tag in values["tags"])
        return changes


class PostForm(RichTextFormMixin, forms.ModelForm):
    """Create or edit a blog post."""

    rich_text_fields: ClassVar[Mapping[str, str]] = {"body": "blog_post"}

    class Meta:
        model = Post
        fields = (
            "title",
            "summary",
            "body",
            "category",
            "tags",
            "access",
            "proposed_price",
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
            "access": forms.Select(attrs={"class": "form-select"}),
            "proposed_price": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0.01"}
            ),
            "allow_comments": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "noindex": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "seo_title": forms.TextInput(attrs={"class": "form-control"}),
            "seo_description": forms.Textarea(
                attrs={"rows": 2, "class": "form-control"}
            ),
            "canonical_url": forms.URLInput(attrs={"class": "form-control"}),
            "hero_image": forms.URLInput(attrs={"class": "form-control"}),
        }
        labels: ClassVar[dict[str, str]] = {
            "access": "Who can read it",
            "proposed_price": "Proposed price (USD)",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "hero_image": (
                "Use a path on this site such as /media/cover.jpg; other hosts are "
                "blocked by the site's content security policy."
            ),
            "access": (
                "Free is open to everyone. Featured and paid show a teaser to readers "
                "without access."
            ),
            "proposed_price": (
                "Needed for paid posts. Staff approve the final price "
                "when they publish."
            ),
        }

    def __init__(self, *args, user=None, require_access: bool = False, **kwargs):
        """Configure editable post fields for the current user."""
        super().__init__(*args, **kwargs)
        allowed = public_tags()
        if self.instance.pk:
            allowed = allowed | self.instance.tags.all()
        tags_field = self.fields["tags"]
        assert isinstance(tags_field, forms.ModelMultipleChoiceField)
        tags_field.queryset = allowed.distinct().order_by("name")
        self.premium_allowed = can_set_premium_access(user)
        choices = [
            (value, label)
            for value, label in PostAccess.choices
            if self.premium_allowed or value == PostAccess.FREE
        ]
        access_field = self.fields["access"]
        assert isinstance(access_field, forms.ChoiceField)
        if require_access:
            # A guide solution must state its access explicitly: no silent default.
            access_field.choices = [("", "Choose who can read it"), *choices]
            access_field.required = True
            access_field.initial = ""
            self.initial.pop("access", None)
            self.fields[
                "proposed_price"
            ].help_text = "Needed for paid solutions; staff approve the final price."
        else:
            access_field.choices = choices
            access_field.required = False
        if not self.premium_allowed:
            del self.fields["proposed_price"]

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        access = cleaned.get("access") or PostAccess.FREE
        cleaned["access"] = access
        if access != PostAccess.FREE and not self.premium_allowed:
            self.add_error(
                "access", "Only approved authors can publish featured or paid posts."
            )
        price = cleaned.get("proposed_price")
        if access == PostAccess.PAID and self.premium_allowed and price is None:
            self.add_error("proposed_price", "A paid post needs a proposed price.")
        if access == PostAccess.FREE:
            cleaned["proposed_price"] = None
        return cleaned
