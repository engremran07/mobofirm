"""Contracts for inline field hints, the password checklist wiring and POST-Redirect-GET.

* Every field rendered by the shared form components owns a ``[data-field-error]``
  container (``role="alert"``, referenced by ``aria-describedby``); server errors and
  in-browser hints share it.
* The lazy modules exist, are registered in ``frontend/src/site.js`` and use only
  CSP-safe patterns (no inline handlers, no native dialogs).
* A successful browser POST never renders a page: it redirects (Confirm Form
  Resubmission is avoided), so no ``is_valid()`` success branch may ``render``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from django import forms
from django.template.loader import render_to_string

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = PROJECT_ROOT / "templates"
FRONTEND = PROJECT_ROOT / "frontend" / "src"
APPS = PROJECT_ROOT / "apps"


class _SampleForm(forms.Form):
    email = forms.EmailField(label="Email")
    agree = forms.BooleanField(label="I agree")
    kind = forms.ChoiceField(choices=[("a", "A")], label="Kind")


def _render(template: str, field_name: str, *, data: dict | None = None) -> str:
    form = _SampleForm(data=data)
    if data is not None:
        form.is_valid()
    return render_to_string(template, {"field": form[field_name]})


@pytest.mark.parametrize(
    "template", ["components/form_field.html", "account/_field.html"]
)
@pytest.mark.parametrize("name", ["email", "agree", "kind"])
def test_every_field_has_an_error_container_wired_to_the_control(template, name):
    html = _render(template, name)

    assert f'id="id_{name}_errors"' in html
    assert 'role="alert"' in html and "data-field-error" in html
    assert f"id_{name}_errors" in re.search(r'aria-describedby="([^"]*)"', html).group(
        1
    )
    # Empty and hidden until a message exists, so nothing is announced up front.
    container = re.search(r"<div[^>]*data-field-error[^>]*>", html).group(0)
    assert " hidden" in container


@pytest.mark.parametrize(
    "template", ["components/form_field.html", "account/_field.html"]
)
def test_server_errors_fill_the_same_container(template):
    html = _render(template, "email", data={"email": "not-an-email"})

    container = re.search(
        r"<div[^>]*data-field-error[^>]*>(.*?)</div>", html, re.DOTALL
    )
    assert container is not None
    assert " hidden" not in container.group(0)
    assert "Enter a valid email address." in container.group(1)
    assert 'aria-invalid="true"' in html


def test_email_inputs_keep_their_type_so_the_browser_can_judge_them():
    assert 'type="email"' in _render("account/_field.html", "email")


@pytest.mark.parametrize(
    "path",
    [
        "account/login.html",
        "account/signup.html",
        "users/tell_us_about_you.html",
        "allauth/elements/form.html",
    ],
)
def test_account_forms_opt_out_of_native_bubbles(path):
    source = (TEMPLATES / path).read_text(encoding="utf-8")

    forms_markup = re.findall(r"<form\b[^>]*>", source)
    assert forms_markup
    assert all("novalidate" in markup for markup in forms_markup)


def test_new_modules_are_registered_and_lazy_in_site_js():
    site = (FRONTEND / "site.js").read_text(encoding="utf-8")

    assert (
        '["passwordChecklist", () => import("./modules/password-checklist.js")' in site
    )
    assert 'import("./modules/inline-validation.js")' in site
    assert 'from "./modules/inline-validation.js"' not in site
    for module in ("password-checklist.js", "inline-validation.js"):
        assert (FRONTEND / "modules" / module).is_file()


def test_new_modules_are_csp_safe_and_never_store_or_log_the_password():
    for module in ("password-checklist.js", "inline-validation.js"):
        source = (FRONTEND / "modules" / module).read_text(encoding="utf-8")
        assert not re.search(r"\b(?:eval|alert|confirm|prompt)\s*\(", source)
        assert "new Function" not in source and "innerHTML" not in source
        assert not re.search(r"(?:localStorage|sessionStorage)", source)
    checklist = (FRONTEND / "modules" / "password-checklist.js").read_text("utf-8")
    assert "console." not in checklist
    assert "X-CSRFToken" in checklist and "document.cookie" not in checklist


def test_checklist_template_is_accessible_and_not_colour_only():
    source = (TEMPLATES / "users" / "includes" / "password_checklist.html").read_text(
        encoding="utf-8"
    )
    styles = (FRONTEND / "styles" / "components" / "_auth.scss").read_text(
        encoding="utf-8"
    )

    assert 'aria-live="polite"' in source and "data-rule-status" in source
    assert "visually-hidden" in source and 'aria-hidden="true"' in source
    for state in ("pass", "fail", "unchecked"):
        assert f'.pw-rule[data-state="{state}"]' in styles
    # Each state has its own glyph, so meaning is never carried by colour alone.
    assert {"\\2713", "\\2715", "\\2013"} <= set(
        re.findall(r'content: "(\\[0-9a-f]+)"', styles)
    )


# --- POST-Redirect-GET -------------------------------------------------------


def _success_branches(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        condition = ast.unparse(node.test)
        if "is_valid()" in condition and not condition.startswith("not "):
            yield node


def _renders(node: ast.AST) -> list[int]:
    lines = []
    for child in ast.walk(node):
        if isinstance(child, ast.Return) and child.value is not None:
            value = ast.unparse(child.value)
            if re.search(r"\b(render|TemplateResponse|render_to_response)\(", value):
                lines.append(child.lineno)
    return lines


def test_no_valid_form_branch_renders_a_page():
    """A valid POST must redirect; re-rendering is reserved for invalid forms."""
    offenders = []
    for path in sorted(APPS.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for branch in _success_branches(tree):
            offenders += [
                f"{path.relative_to(PROJECT_ROOT)}:{line}"
                for line in _renders(ast.Module(body=branch.body, type_ignores=[]))
            ]
    assert not offenders, f"Successful POST renders instead of redirecting: {offenders}"


def test_the_scanner_flags_a_rendering_success_branch():
    source = (
        "def view(request):\n"
        "    if form.is_valid():\n"
        "        form.save()\n"
        "        return render(request, 'done.html')\n"
        "    return render(request, 'form.html')\n"
    )
    branches = list(_success_branches(ast.parse(source)))

    assert len(branches) == 1
    assert _renders(ast.Module(body=branches[0].body, type_ignores=[])) == [4]
