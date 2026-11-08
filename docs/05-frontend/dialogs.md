# App-owned dialogs

The site never uses the browser's native `alert()`, `confirm()` or `prompt()`, the HTMX confirm attribute (which calls the native confirm) or inline confirm handlers. Every dialog is an app-owned, accessible modal built on one shared shell. `tests/backend/test_no_native_dialogs.py` fails the build on any native call.

## Ownership rule: core = shell, app = content

| Layer | Owns | Lives in |
| --- | --- | --- |
| **core** (shell) | native `<dialog>` markup, the Alpine `appDialog` component, `#dialog-slot`, focus/scroll/Esc/backdrop behaviour, CSS, the `DialogView`/`DialogForm` helpers, the JavaScript `Dialogs` API, the unsaved-changes guard | `templates/components/dialog*.html`, `templates/components/confirm_dialog.html`, `frontend/src/modules/dialogs.js`, `frontend/src/styles/dialogs.css`, `apps/core/dialogs.py` |
| **app** (content) | the copy, form, view, permission rule and side effect of one dialog | `templates/<app>/dialogs/*.html`, `apps/<app>/dialogs.py` (or `forms.py`) |

Core never imports a domain app for dialogs. An app subclasses `DialogView`, extends `components/dialog.html` and registers a URL in its own `urls.py`. Staff dialogs in the Unfold admin keep using Unfold's own action dialog (`apps/core/admin_base.py`: `confirm_dialog`, `BaseDialogForm`); they are not affected by this shell.

Converted so far: blog (publish, schedule, archive), comments (reject, mark as spam with a note), tags (reject suggestion, merge), promotions (claim an offer). Shop, marketplace, firmwares and users add their dialogs the same way.

## Pieces

- `templates/components/dialog.html` – the shell. Extend it and fill `dialog_body` (and optionally `dialog_footer`). Context: `dialog_id`, `dialog_title`, `dialog_message`, `dialog_size` (`sm`/`md`/`lg`), `danger`, `static_backdrop`, `esc_blocked`, `confirm_label`, `cancel_label`, `dialog_action`, `dialog_hx`, `unsaved_guard`, `confirm_phrase`, `auto_open`, `dialog_inline`.
- `templates/components/dialog_form.html` – generic body that renders every field of `form`.
- `templates/components/confirm_dialog.html` – the standard confirm (see below).
- `templates/components/dialog_js_template.html` – markup cloned by JavaScript confirms. Included once by `base.html`.
- `#dialog-slot` in `base.html` – where HTMX dialogs are loaded.
- `apps/core/dialogs.py` – `DialogView`, `DialogForm`, `ConfirmDialogForm`, `DialogError`, `dialog_success`.
- `frontend/src/modules/dialogs.js` – `appDialog` (Alpine CSP component) and the `Dialogs` API.

## Server-rendered dialogs (HTMX)

```python
# apps/comments/dialogs.py
class ModerationDialog(DialogView):
    template_name = "comments/dialogs/moderate.html"   # extends components/dialog.html
    form_class = ModerationNoteForm                   # DialogForm; reason_required = False
    danger = True

    def has_permission(self, request): return request.user.has_perm("comments.change_comment")
    def perform(self, form): moderate_comment(self.comment, self.action, self.request.user, note=form.reason)
```

Open it from anywhere. Keep a real `href`: without JavaScript it is a normal page.

```html
<a href="{% url 'comments:moderation_dialog' comment_id=c.pk action='reject' %}"
   hx-get="{% url 'comments:moderation_dialog' comment_id=c.pk action='reject' %}"
   hx-target="#dialog-slot" hx-swap="innerHTML">Reject</a>
```

Request/response convention:

1. `GET` returns the dialog fragment (`auto_open`), or without HTMX a full page with the same form rendered open (`dialog_inline`).
2. `POST` validates the `DialogForm`. Invalid: HTTP 200 with the dialog re-rendered (`hx-target="closest dialog"`, `outerHTML`), errors inside it; focus return to the original opener is preserved. A `DialogError` raised from `perform` is shown as a form error and the transaction rolls back.
3. Valid: `perform` runs in `transaction.atomic()`; the response is `204` with `HX-Trigger: {"dialog:close": {}, "dialog:done": {...payload}}` plus `HX-Redirect` (`success_url` or a safe `next`) or `HX-Refresh`. Set `refresh_on_success = False` to stay on the page and let listeners update it (`hx-trigger="dialog:done from:body"`). Without HTMX it is a POST-Redirect-GET.
4. Anonymous users get `HX-Redirect` to sign-in (HTMX) or a redirect. `has_permission` returning `False` gives 403 (the global feedback region explains it). CSRF is enforced by the global middleware; the form carries the token and the body sends `X-CSRFToken` for HTMX.

`DialogForm` can add `reason` (`reason_required = True/False`, `reason_label`) and a typed `confirm_phrase`; the phrase is enforced server-side and the client keeps the submit button disabled until it matches.

## Standard confirm (`components/confirm_dialog.html`)

Inline `<dialog>` with a CSRF POST form, opened by `<button type="button" data-dialog-open="<dialog_id>">`.

```django
{% include "components/confirm_dialog.html" with dialog_id="remove-x" dialog_title="Remove X?" dialog_message="This cannot be undone." dialog_action=remove_url confirm_label="Remove" danger=True reason_label="Why?" reason_required=True confirm_phrase="REMOVE" hidden_name="id" hidden_value=x.pk %}
```

`danger` gives the destructive style and makes the backdrop non-dismissing (Esc still closes, for accessibility; pass `esc_blocked=True` only when closing mid-operation would be harmful). The POST handler must still validate everything; use a `DialogForm` for `reason`/`confirmation`.

## JavaScript API

`window.Dialogs` (also exported from `dialogs.js`):

- `Dialogs.confirm({ title, message, confirmLabel, cancelLabel, danger, phrase })` → `Promise<boolean>`; replaces `confirm()`.
- `Dialogs.confirmWithReason({ ..., reason: { label, required } })` → `Promise<string | null>` (trimmed reason, `null` if cancelled).
- `Dialogs.open(url)` – load a server dialog into `#dialog-slot`.
- `Dialogs.close()` – close open app dialogs.

Declarative confirms (no JavaScript to write):

- `data-confirm-dialog="Message"` on a submit button, form or link (plus `data-confirm-title`, `data-confirm-label`, `data-confirm-cancel`, `data-confirm-danger`).
- The `htmx:confirm` event is intercepted so an HTMX element carrying `data-confirm-dialog` asks with the app dialog before the request is issued. If anything still sets the native HTMX confirm question it is routed to the app dialog too, never the browser prompt.

## Unsaved-changes guard

Mark a form `data-unsaved-guard`. After the first edit:

- in-app link clicks, other form submissions and HTMX requests that would replace the form show a "Discard unsaved changes?" app dialog (override the copy with `data-unsaved-title|message|confirm|cancel`);
- closing a dialog that contains a dirty guarded form asks first;
- submitting or resetting the form clears the flag.

**The one documented exception.** A single `beforeunload` listener in `dialogs.js` remains for tab close, refresh and address-bar navigation. Browsers do not allow custom UI there, so the generic browser prompt is requested only while a guarded form is dirty. It is the only allowed use; the guard test asserts there is exactly one.

## Behaviour and accessibility checklist

- Native `<dialog>` + `showModal()`: top layer, inert page behind, `::backdrop`, Esc.
- Explicit Tab/Shift+Tab trap inside the dialog (native modals can tab out to the browser UI).
- `aria-labelledby` (title) and `aria-describedby` (message); close button has an accessible name.
- Initial focus: `[autofocus]`, else first field, else the safe action (Cancel for destructive, the primary action otherwise). Focus returns to the opener on close, or to `#main-content` if it is gone.
- Esc and backdrop close unless configured otherwise (`static_backdrop`, `esc_blocked`); a drag that starts inside and ends on the backdrop does not close it.
- Scroll lock via `html:has(.app-dialog:modal)`; no layout shift.
- Below 576px the dialog is a full-width bottom sheet; above it a centred panel (`sm` 30rem, `md` 40rem, `lg` 56rem). Light/dark use the shared tokens. `prefers-reduced-motion` disables the entrance animation.
- Strict CSP: no inline scripts or styles, no `eval`; Alpine uses the CSP build, handlers are named methods.
- Without JavaScript the dialog URL renders the same form inline in a page and the POST redirects after success.

## Testing

- `tests/backend/core/test_dialogs.py` – the `DialogView`/component contract (permission, CSRF, validation, rollback, redirects).
- `tests/backend/{blog,comments,tags}/test_*_dialogs.py` – each converted dialog.
- `tests/backend/test_no_native_dialogs.py` – forbids native dialogs and checks the single `beforeunload`.
- `tests/frontend/test_dialogs_browser.py` – Playwright: open/close/Esc/focus trap and return, HTMX validation, destructive confirm, unsaved-changes guard, bottom sheet, dark theme, reduced motion, 360/1440 overflow, no console/CSP errors, no native dialogs, no-JavaScript fallback.
