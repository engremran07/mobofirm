"""Forms and widgets of the staff console (Django-only).

Widgets: ``ConsoleTextInputWidget``, ``ConsoleTextareaWidget``, ``ConsoleIntegerWidget``,
``ConsolePasswordWidget``, ``ConsoleSelectWidget``, ``ConsoleSelectMultipleWidget``,
``ConsoleCheckboxSelectMultipleWidget``, ``ConsoleRadioSelectWidget`` and
``ConsoleSplitDateTimeWidget``. Forms: ``BaseDialogForm``, ``ActionForm``,
``UserChangeForm``, ``UserCreationForm`` and ``AdminPasswordChangeForm``.

Widget templates (authored under ``templates/console/widgets/`` and found through
``FORM_RENDERER = TemplatesSetting``): each widget's ``template_name`` is
``console/widgets/<name>.html`` where ``<name>`` is ``text``, ``textarea``,
``integer``, ``password``, ``select``, ``select_multiple``,
``checkbox_select_multiple``, ``radio_select`` or ``split_datetime``. The widgets also
carry their Bootstrap classes in ``attrs`` (``form-control`` / ``form-select``), so a
template may simply include Django's stock markup. Option inputs of the checkbox and
radio groups get ``form-check-input``; their wrapper gets ``console-choice-list``.

``BaseDialogForm`` and the other forms add the matching Bootstrap class to any field
whose widget has no ``class`` yet (:func:`style_form_widgets`), so plain Django
widgets inside dialogs look right as well.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from django import forms
from django.contrib.admin import helpers
from django.contrib.auth import forms as auth_forms
from django.forms.widgets import ChoiceWidget
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

__all__ = [
    "ActionForm",
    "AdminPasswordChangeForm",
    "BaseDialogForm",
    "ConsoleCheckboxSelectMultipleWidget",
    "ConsoleIntegerWidget",
    "ConsolePasswordWidget",
    "ConsoleRadioSelectWidget",
    "ConsoleSelectMultipleWidget",
    "ConsoleSelectWidget",
    "ConsoleSplitDateTimeWidget",
    "ConsoleTextInputWidget",
    "ConsoleTextareaWidget",
    "UserChangeForm",
    "UserCreationForm",
    "merge_css_classes",
    "style_form_widgets",
]


def merge_css_classes(*groups: str | None) -> str:
    """Join CSS class strings, dropping blanks and duplicates (order kept)."""
    seen: dict[str, None] = {}
    for group in groups:
        for name in (group or "").split():
            seen.setdefault(name, None)
    return " ".join(seen)


class ConsoleWidgetMixin(forms.Widget):
    """Adds the widget's Bootstrap class at render time without clobbering ``class``."""

    css_class: ClassVar[str] = "form-control"

    def build_attrs(
        self, base_attrs: dict[str, Any], extra_attrs: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        attrs = super().build_attrs(base_attrs, extra_attrs)
        attrs["class"] = merge_css_classes(
            self.css_class,
            base_attrs.get("class"),
            (extra_attrs or {}).get("class"),
        )
        return attrs


class ConsoleTextInputWidget(ConsoleWidgetMixin, forms.TextInput):
    template_name = "console/widgets/text.html"


class ConsoleTextareaWidget(ConsoleWidgetMixin, forms.Textarea):
    template_name = "console/widgets/textarea.html"


class ConsoleIntegerWidget(ConsoleWidgetMixin, forms.NumberInput):
    template_name = "console/widgets/integer.html"


class ConsolePasswordWidget(ConsoleWidgetMixin, forms.PasswordInput):
    """Never renders the stored value back (Django's default ``render_value=False``)."""

    template_name = "console/widgets/password.html"


class ConsoleSelectWidget(ConsoleWidgetMixin, forms.Select):
    css_class = "form-select"
    template_name = "console/widgets/select.html"


class ConsoleSelectMultipleWidget(ConsoleWidgetMixin, forms.SelectMultiple):
    css_class = "form-select"
    template_name = "console/widgets/select_multiple.html"


class _ChoiceGroupMixin(ConsoleWidgetMixin, ChoiceWidget):
    """Checkbox/radio groups: the wrapper is a list, each input a Bootstrap check."""

    css_class = "console-choice-list"

    def create_option(
        self,
        name: str,
        value: Any,
        label: Any,
        selected: Any,
        index: int,
        subindex: int | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        option["attrs"]["class"] = "form-check-input"
        return option


class ConsoleCheckboxSelectMultipleWidget(
    _ChoiceGroupMixin, forms.CheckboxSelectMultiple
):
    template_name = "console/widgets/checkbox_select_multiple.html"


class ConsoleRadioSelectWidget(_ChoiceGroupMixin, forms.RadioSelect):
    template_name = "console/widgets/radio_select.html"


class ConsoleSplitDateTimeWidget(ConsoleWidgetMixin, forms.SplitDateTimeWidget):
    """Date and time as two native inputs (both carry ``form-control``)."""

    template_name = "console/widgets/split_datetime.html"


def style_form_widgets(form: forms.BaseForm) -> None:
    """Give widgets without a ``class`` the Bootstrap class matching their kind.

    Idempotent; hidden inputs are left alone and explicit classes always win.
    """
    for field in form.fields.values():
        widget = field.widget
        if (
            widget.is_hidden
            or widget.attrs.get("class")
            or isinstance(widget, ConsoleWidgetMixin)
        ):
            continue
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs["class"] = "form-check-input"
        elif isinstance(widget, forms.CheckboxSelectMultiple | forms.RadioSelect):
            continue
        elif isinstance(widget, forms.Select | forms.SelectMultiple):
            widget.attrs["class"] = "form-select"
        else:
            widget.attrs["class"] = "form-control"


class BaseDialogForm(forms.Form):
    """Form shown in the confirmation dialog of an admin action.

    ``request`` and ``object_id`` (``None`` for list actions) are keyword arguments:
    ``form_class(data=..., request=..., object_id=...)``.
    ``_form_submitted`` makes an empty POST (a confirm-only dialog) valid. Optional
    ``form_before_template`` / ``form_after_template`` render extra context around the
    fields (see ``get_before_template_context`` / ``get_after_template_context``).
    """

    form_before_template: str | None = None
    form_after_template: str | None = None

    _form_submitted = forms.BooleanField(
        required=True, initial=True, widget=forms.HiddenInput
    )

    def __init__(
        self,
        request: HttpRequest,
        object_id: int | str | None = None,
        **kwargs: Any,
    ) -> None:
        self.request = request
        self.object_id = object_id
        super().__init__(**kwargs)
        style_form_widgets(self)

    def get_before_template_context(
        self, request: HttpRequest, _object_id: int | str | None = None
    ) -> dict[str, Any]:
        return {}

    def get_after_template_context(
        self, request: HttpRequest, _object_id: int | str | None = None
    ) -> dict[str, Any]:
        return {}

    def render_before_template(self) -> str:
        if not self.form_before_template:
            return ""
        return render_to_string(
            self.form_before_template,
            self.get_before_template_context(self.request, self.object_id),
            request=self.request,
        )

    def render_after_template(self) -> str:
        if not self.form_after_template:
            return ""
        return render_to_string(
            self.form_after_template,
            self.get_after_template_context(self.request, self.object_id),
            request=self.request,
        )


class ActionForm(helpers.ActionForm):
    """Changelist bulk-action form: the action picker plus ``select_across``.

    Subclass it and set ``action_form`` on a ``ModelAdmin`` to ask for extra inputs
    that the selected bulk action reads from ``request.POST``.
    """

    action = forms.ChoiceField(
        label="",
        widget=ConsoleSelectWidget(
            attrs={
                "aria-label": _("Select action to run"),
                "data-console-bulk-action": "",
            }
        ),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        style_form_widgets(self)


class UserCreationForm(auth_forms.AdminUserCreationForm):
    """Admin "add user" form with console-styled password and password-mode fields."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        style_form_widgets(self)
        if "usable_password" in self.fields:
            usable = cast(forms.ChoiceField, self.fields["usable_password"])
            choices = usable.choices
            usable.widget = ConsoleRadioSelectWidget()
            usable.choices = choices


class UserChangeForm(auth_forms.UserChangeForm):
    """Admin user change form (Django's read-only password hash display + link)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        style_form_widgets(self)


class AdminPasswordChangeForm(auth_forms.AdminPasswordChangeForm):
    """Staff sets another account's password."""

    def __init__(self, user: Any, *args: Any, **kwargs: Any) -> None:
        super().__init__(user, *args, **kwargs)
        style_form_widgets(self)
