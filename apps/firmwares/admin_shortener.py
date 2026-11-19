"""Console pages of the external download link shortener.

* :class:`ShortenerSettingsAdmin`: the singleton settings page (mode, provider, write-only
  API key, health-check button, circuit state). Needs ``firmwares.change_shortenersettings``.
* Read-only audit pages: first-party links with their daily clicks, cached provider links
  and the outcome log.
* :class:`FirmwareShortenerOverrideAdmin`: per-firmware override (the per-brand choice is
  on the brand page).
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from django import forms
from django.contrib import messages
from django.contrib.admin import register
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet, Sum
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.admin_base import (
    BaseDialogForm,
    ConfirmForm,
    ModelAdmin,
    ReadOnlyModelAdmin,
    SingletonModelAdmin,
    action,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)
from apps.core.admin_forms import ConsoleSelectWidget, ConsoleTextInputWidget

from .catalog import PUBLIC_FIRMWARE_TYPES
from .shortener import THIRD_PARTY_MODES, check_provider
from .shortener_constants import HttpMethod, ProviderKind
from .shortener_models import (
    DownloadShortLink,
    FirmwareShortenerOverride,
    ShortenerOutcome,
    ShortenerSettings,
    ThirdPartyShortLink,
)
from .shortener_providers import body_template_errors, endpoint_errors

RESPONSE_PATH = re.compile(r"^[A-Za-z0-9_.-]*$")
# Changing any of these makes cached provider links and the circuit state stale.
PROVIDER_FIELDS = frozenset(
    {
        "provider_kind",
        "provider_endpoint",
        "provider_method",
        "provider_auth_header",
        "provider_auth_prefix",
        "provider_body_template",
        "provider_response_path",
        "new_api_key",
        "clear_api_key",
    }
)
RESULT_BADGES = {
    "created": "success",
    "ok": "success",
    "failed": "danger",
    "breaker_open": "warning",
}

MODE_HELP = _(
    "Off: the plain link is shared. First-party: your own /d/<code>/ link, which sends "
    "the visitor through the download page (countdown and the optional ad page) before "
    "the file host. It earns indirectly through that ad page, gives you attribution "
    "(clicks per day, no personal data) and keeps the link on your domain. Third-party: "
    "a shortening service shortens the download page link. Both: the first-party link "
    "is shortened by the provider. Third-party shorteners that pay per visitor usually "
    "pay little, add one more step for the visitor and their links are often flagged "
    "by Safe Browsing and ad networks, which can hurt trust and ad approval. Run the "
    "health check, try a few links, and keep the first-party option as the fallback: "
    "if the provider fails the shared link falls back automatically."
)


class ShortenerSettingsForm(forms.ModelForm):
    """Provider settings with a write-only API key that is stored encrypted."""

    new_api_key = forms.CharField(
        label=_("API key"),
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False, attrs={"autocomplete": "new-password"}
        ),
    )
    clear_api_key = forms.BooleanField(
        label=_("Remove the stored API key"), required=False
    )

    class Meta:
        model = ShortenerSettings
        fields: ClassVar[tuple[str, ...]] = (
            "mode",
            "provider_kind",
            "provider_endpoint",
            "provider_method",
            "provider_auth_header",
            "provider_auth_prefix",
            "provider_body_template",
            "provider_response_path",
            "timeout_seconds",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.has_api_key:
            self.fields["new_api_key"].help_text = _(
                "A key is stored (encrypted, never shown). Leave blank to keep it."
            )
        else:
            self.fields["new_api_key"].help_text = _(
                "Stored encrypted and never shown again."
            )

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        kind = cleaned.get("provider_kind")
        mode = cleaned.get("mode")
        endpoint = (cleaned.get("provider_endpoint") or "").strip()
        uses_provider = mode in THIRD_PARTY_MODES
        if kind in {ProviderKind.QUERY_API, ProviderKind.JSON_API} and (
            uses_provider or endpoint
        ):
            for problem in endpoint_errors(endpoint, kind=kind):
                self.add_error("provider_endpoint", problem)
        if (
            uses_provider
            and kind == ProviderKind.JSON_API
            and cleaned.get("provider_method") == HttpMethod.POST
        ):
            for problem in body_template_errors(
                cleaned.get("provider_body_template", "")
            ):
                self.add_error("provider_body_template", problem)
        if not RESPONSE_PATH.match(cleaned.get("provider_response_path") or ""):
            self.add_error(
                "provider_response_path", _("Use letters, digits, dots and dashes.")
            )
        self._apply_key(
            cleaned, kind=kind, uses_provider=uses_provider, endpoint=endpoint
        )
        return cleaned

    def _apply_key(
        self,
        cleaned: dict[str, Any],
        *,
        kind: object,
        uses_provider: bool,
        endpoint: str,
    ) -> None:
        new_key = cleaned.get("new_api_key") or ""
        try:
            if cleaned.get("clear_api_key"):
                self.instance.set_api_key("")
            elif new_key:
                self.instance.set_api_key(new_key)
        except ImproperlyConfigured:
            self.add_error(
                "new_api_key",
                _(
                    "The key cannot be encrypted because FERNET_KEY is not configured "
                    "on the server. Nothing was saved."
                ),
            )
            return
        needs_key = kind == ProviderKind.QUERY_API or "{key}" in (
            f"{endpoint} {cleaned.get('provider_body_template') or ''}"
        )
        if uses_provider and needs_key and not self.instance.has_api_key:
            self.add_error("new_api_key", _("This provider needs an API key."))


@register(ShortenerSettings)
class ShortenerSettingsAdmin(SingletonModelAdmin):
    """Mode, provider and health of the external download link shortener."""

    form = ShortenerSettingsForm
    actions_detail = ("check_provider",)
    readonly_fields = ("provider_state", "last_check_summary")
    fieldsets = (
        (
            _("1 · What is shared"),
            {"fields": ("mode",), "classes": ["tab"], "description": MODE_HELP},
        ),
        (
            _("2 · Third-party provider"),
            {
                "classes": ["tab"],
                "description": _(
                    "Used by the third-party and both modes. The provider only ever "
                    "receives a link on this site, never the file host address. "
                    "Requests go through the SSRF-safe fetcher (public https hosts "
                    "only). Changing anything here empties the cached provider links."
                ),
                "fields": (
                    "provider_kind",
                    "provider_endpoint",
                    "new_api_key",
                    "clear_api_key",
                    "provider_method",
                    "provider_auth_header",
                    "provider_auth_prefix",
                    "provider_body_template",
                    "provider_response_path",
                    "timeout_seconds",
                ),
            },
        ),
        (
            _("3 · Health"),
            {
                "classes": ["tab"],
                "description": _(
                    "Use “Test provider” at the top of this page after saving. After "
                    "five failures in a row the provider is paused for 15 minutes and "
                    "shared links fall back to the first-party or plain link."
                ),
                "fields": ("provider_state", "last_check_summary"),
            },
        ),
    )
    conditional_fields: ClassVar[dict[str, str]] = {
        "provider_method": "provider_kind == 'json_api'",
        "provider_auth_header": "provider_kind == 'json_api'",
        "provider_auth_prefix": "provider_kind == 'json_api'",
        "provider_body_template": "provider_kind == 'json_api'",
        "provider_endpoint": "provider_kind != 'isgd' && provider_kind != 'tinyurl'",
    }

    @display(description=_("Provider status"))
    def provider_state(self, obj: ShortenerSettings | None) -> str:
        if obj is None or not obj.pk:
            return "—"
        problem = obj.configuration_problem
        if problem:
            return str(_("Not ready: %(problem)s")) % {"problem": problem}
        if obj.breaker_open_until and obj.breaker_open_until > timezone.now():
            return str(_("Paused until %(when)s after repeated failures.")) % {
                "when": f"{obj.breaker_open_until:%Y-%m-%d %H:%M}"
            }
        return str(_("Ready (%(failures)s failure(s) in a row).")) % {
            "failures": obj.breaker_failures
        }

    @display(description=_("Last health check"))
    def last_check_summary(self, obj: ShortenerSettings | None) -> str:
        if obj is None or obj.last_check_at is None:
            return str(_("Not tested yet."))
        verdict = "OK" if obj.last_check_ok else "Failed"
        return (
            f"{verdict}: {obj.last_check_message} ({obj.last_check_at:%Y-%m-%d %H:%M})"
        )

    def save_model(
        self, request: HttpRequest, obj: ShortenerSettings, form: Any, change: bool
    ) -> None:
        """Save only the edited columns so runtime state is never overwritten."""
        provider_changed = bool(PROVIDER_FIELDS & set(form.changed_data))
        columns = [*ShortenerSettingsForm.Meta.fields, "provider_api_key_encrypted"]
        if provider_changed:
            obj.cache_epoch += 1
            obj.breaker_failures = 0
            obj.breaker_open_until = None
            columns += ["cache_epoch", "breaker_failures", "breaker_open_until"]
        if obj.pk is None:
            obj.save()
        else:
            obj.save(update_fields=columns)

    @action(
        description=_("Test provider"),
        icon="wifi_tethering",
        permissions=["change"],
        url_path="check-provider",
        dialog=confirm_dialog(
            _("Test the provider"),
            _(
                "Asks the provider to shorten this site's home page once. It creates "
                "one real short link at the provider and changes nothing here."
            ),
            ConfirmForm,
            submit=_("Test"),
        ),
    )
    def check_provider(
        self, request: HttpRequest, _form: BaseDialogForm, **_kwargs: Any
    ) -> HttpResponse:
        outcome = check_provider()
        self.message_user(
            request,
            outcome.message,
            messages.SUCCESS if outcome.ok else messages.WARNING,
        )
        return htmx_redirect(
            return_url(request, reverse("admin:firmwares_shortenersettings_change"))
        )


@register(DownloadShortLink)
class DownloadShortLinkAdmin(ReadOnlyModelAdmin):
    list_display = (
        "code",
        "firmware_type",
        "firmware_id",
        "clicks",
        "bot_clicks",
        "created_at",
    )
    list_filter = ("firmware_type",)
    search_fields = ("code", "firmware_id")

    def get_queryset(self, request: HttpRequest) -> QuerySet[DownloadShortLink]:
        return (
            super()
            .get_queryset(request)
            .annotate(
                click_total=Sum("days__clicks"), bot_total=Sum("days__bot_clicks")
            )
        )

    @display(description=_("Clicks"), ordering="click_total")
    def clicks(self, obj: DownloadShortLink) -> int:
        return int(getattr(obj, "click_total", 0) or 0)

    @display(description=_("Bot / preview hits"), ordering="bot_total")
    def bot_clicks(self, obj: DownloadShortLink) -> int:
        return int(getattr(obj, "bot_total", 0) or 0)


@register(ThirdPartyShortLink)
class ThirdPartyShortLinkAdmin(ReadOnlyModelAdmin):
    list_display = ("short_url", "provider_kind", "target_url", "created_at")
    list_filter = ("provider_kind",)
    search_fields = ("short_url", "target_url")


@register(ShortenerOutcome)
class ShortenerOutcomeAdmin(ReadOnlyModelAdmin):
    list_display = (
        "created_at",
        "stage",
        "provider_kind",
        "result_badge",
        "code",
        "target_host",
        "latency_ms",
    )
    list_filter = ("stage", "result", "provider_kind")
    search_fields = ("code", "target_host", "firmware_id")

    @display(description=_("Result"), ordering="result", label=RESULT_BADGES)
    def result_badge(self, obj: ShortenerOutcome) -> str:
        return obj.result


class FirmwareShortenerOverrideForm(forms.ModelForm):
    firmware_type = forms.ChoiceField(
        choices=[(key, key.title()) for key, _model in PUBLIC_FIRMWARE_TYPES],
        widget=ConsoleSelectWidget,
    )

    class Meta:
        model = FirmwareShortenerOverride
        fields = ("firmware_type", "firmware_id", "mode")
        widgets: ClassVar[dict[str, Any]] = {"firmware_id": ConsoleTextInputWidget}


@register(FirmwareShortenerOverride)
class FirmwareShortenerOverrideAdmin(ModelAdmin):
    """Force one firmware's shortening mode (beats the brand and the global mode)."""

    form = FirmwareShortenerOverrideForm
    list_display = ("firmware_type", "firmware_id", "mode", "updated_at")
    list_filter = ("mode", "firmware_type")
    search_fields = ("firmware_id",)

    def get_readonly_fields(
        self, request: HttpRequest, obj: FirmwareShortenerOverride | None = None
    ) -> tuple[str, ...]:
        fixed = tuple(super().get_readonly_fields(request, obj))
        return (*fixed, "firmware_type", "firmware_id") if obj is not None else fixed
