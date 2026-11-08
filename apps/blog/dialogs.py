"""Editorial workflow dialogs (publish, schedule, archive): blog owns the content."""

from __future__ import annotations

from functools import cached_property
from typing import Any

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.blog import credits as credit_rules
from apps.blog import guide_requests, shop_gateway
from apps.blog.access import blog_enabled_for, can_manage_post
from apps.blog.models import GuideRequest, Post
from apps.blog.paywall import PostGate, resolve_gate
from apps.blog.views_manage import run_workflow
from apps.core.dialogs import ConfirmDialogForm, DialogError, DialogForm, DialogView


class ScheduleForm(DialogForm):
    """The time a post is published automatically."""

    when = forms.DateTimeField(
        label=_("Publish on"),
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
    )

    def clean_when(self):
        """Scheduling in the past is a mistake, not a publish-now."""
        when = self.cleaned_data["when"]
        if when <= timezone.now():
            raise forms.ValidationError(_("Choose a time in the future."))
        return when


COPY: dict[str, dict[str, Any]] = {
    "publish": {
        "title": _("Publish this post?"),
        "message": _("It goes live immediately and appears in feeds and search."),
        "confirm": _("Publish"),
        "danger": False,
    },
    "archive": {
        "title": _("Archive this post?"),
        "message": _("It is removed from the public site. You can restore it later."),
        "confirm": _("Archive"),
        "danger": True,
    },
    "schedule": {
        "title": _("Schedule this post"),
        "message": _("It is published automatically at the time you choose."),
        "confirm": _("Schedule"),
        "danger": False,
    },
}


class PostWorkflowDialog(DialogView):
    """Confirm one workflow transition (``action`` in the URL) for one post."""

    template_name = "blog/dialogs/post_workflow.html"
    success_url_name = "blog:manage_posts"
    result_message = ""

    @cached_property
    def action(self) -> str:
        """The workflow action named in the URL."""
        action = str(self.kwargs["action"])
        if action not in COPY:
            raise Http404
        return action

    @cached_property
    def post_obj(self) -> Post:
        """The post the action applies to."""
        return get_object_or_404(Post, slug__iexact=self.kwargs["slug"])

    def has_permission(self, request: HttpRequest) -> bool:
        """Only users who can manage this post (others get a 404)."""
        if not can_manage_post(request.user, self.post_obj):
            raise Http404
        return bool(self.action)

    def get_form_class(self) -> type[DialogForm]:
        """Scheduling asks for a time; the other actions only confirm."""
        return ScheduleForm if self.action == "schedule" else ConfirmDialogForm

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        """Add the per-action copy and the post."""
        context = super().get_context_data(form)
        copy = COPY[self.action]
        context.update(
            dialog_id="post-workflow-dialog",
            dialog_title=copy["title"],
            dialog_message=copy["message"],
            confirm_label=copy["confirm"],
            danger=copy["danger"],
            static_backdrop=copy["danger"],
            post=self.post_obj,
        )
        return context

    def perform(self, form: DialogForm) -> None:
        """Run the workflow; its refusals are shown inside the dialog."""
        try:
            result = run_workflow(
                self.post_obj,
                self.action,
                self.request.user,
                form.cleaned_data.get("when"),
            )
        except PermissionDenied as error:
            raise DialogError(
                str(error) or "You do not have permission for that action."
            ) from error
        except ValidationError as error:
            raise DialogError(" ".join(error.messages)) from error
        self.result_message = f"{self.post_obj.title}: {result.message}."

    def get_success_message(self) -> str:
        """The workflow result, e.g. "Post: Published"."""
        return self.result_message


class PurchasePostDialog(DialogView):
    """Pay credits once for a featured or paid article (the shop does the charge)."""

    template_name = "blog/dialogs/purchase_post.html"
    dialog_id = "purchase-post-dialog"
    dialog_title = _("Unlock this article")
    confirm_label = _("Pay with credits")
    refresh_on_success = False

    @cached_property
    def post_obj(self) -> Post:
        """The live, gated post named in the URL."""
        post = get_object_or_404(Post.objects.live(), slug__iexact=self.kwargs["slug"])
        if not post.is_gated:
            raise Http404
        return post

    @cached_property
    def gate(self) -> PostGate:
        """What the signed-in reader may already see."""
        return resolve_gate(self.request.user, self.post_obj)

    def has_permission(self, request: HttpRequest) -> bool:
        """The blog must be on for this reader."""
        if not blog_enabled_for(request.user):
            raise Http404
        return True

    def get_success_url(self) -> str | None:
        """Back to the article, which now renders in full."""
        return self.post_obj.get_absolute_url()

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        """Show the price, the balance and where to earn more credits."""
        context = super().get_context_data(form)
        balance = credit_rules.balance_of(self.request.user)
        price = self.gate.credit_price
        context.update(
            post=self.post_obj,
            gate=self.gate,
            balance=balance,
            insufficient=bool(
                price is not None and balance is not None and balance < price
            ),
            earn_url=credit_rules.earn_url(),
        )
        return context

    def perform(self, form: DialogForm) -> None:
        """Charge once; an existing right to read (package, earlier purchase) is free."""
        if not self.gate.locked:
            return
        if not self.gate.can_buy:
            raise DialogError(
                self.gate.reason or "This article cannot be bought with credits."
            )
        result = shop_gateway.purchase_featured_blog(
            self.request.user,
            self.post_obj.pk,
            self.post_obj.price,
            key=f"blog-featured:{self.request.user.pk}:{self.post_obj.pk}",
        )
        if not result.allowed:
            raise DialogError(result.reason or "The purchase could not be completed.")

    def get_success_message(self) -> str:
        """Confirmation shown on the unlocked article."""
        return f"“{self.post_obj.title}” is unlocked."


class CancelGuideRequestDialog(DialogView):
    """Requester (or staff) withdraws a request; attached bounties are refunded."""

    form_class = ConfirmDialogForm
    dialog_id = "cancel-guide-request-dialog"
    dialog_title = _("Cancel this request?")
    dialog_message = _(
        "It is withdrawn from the site and any bounty credits return to their posters."
    )
    confirm_label = _("Cancel request")
    cancel_label = _("Keep it")
    danger = True
    success_message = _("Request cancelled.")

    @cached_property
    def request_obj(self) -> GuideRequest:
        """The request named in the URL."""
        return get_object_or_404(GuideRequest, pk=self.kwargs["pk"])

    def has_permission(self, request: HttpRequest) -> bool:
        """Only the requester or reviewing staff; others get a 404."""
        user = request.user
        allowed = user.pk == self.request_obj.requester_id or (
            user.is_staff and user.has_perm("blog.change_guiderequest")
        )
        if not allowed:
            raise Http404
        return True

    def get_success_url(self) -> str | None:
        """Back to the request page."""
        return self.request_obj.get_absolute_url()

    def perform(self, form: DialogForm) -> None:
        """Cancel; the state machine refuses finished requests."""
        try:
            guide_requests.cancel(
                self.request_obj.pk, actor=self.request.user, reason="Cancelled"
            )
        except guide_requests.GuideRequestError as error:
            raise DialogError(str(error)) from error
