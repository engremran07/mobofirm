# Dialog audit (Wave 10)

Owner requirement: "the popup window is not complete". This audit lists every dialog kind in the ecosystem, the checklist each must meet, what the shared shell now guarantees, and what each app-specific body still has to migrate.

## Checklist (the definition of a complete dialog)

title + close button · description · scrollable body with edge shadows · sticky footer actions (secondary / primary / danger) · size variants (sm, md, lg, xl; bottom sheet on phones) · focus trap and focus restore · ESC and backdrop rules · loading state on submit · server error display · success feedback (toast) · nested safety · scroll lock without layout shift · reduced motion · dark mode · long-text wrapping · labelled form fields.

## Shell guarantees (`components/dialog.html`, `components/_dialog.scss`, `modules/dialogs.js`, `apps/core/dialogs.py`)

| Requirement | How the shell meets it |
| --- | --- |
| Title, close, description | header `h2` + `.btn-close`, `dialog_message` wired with `aria-labelledby` / `aria-describedby` |
| Scrollable body + edge shadows | only `.app-dialog__body` scrolls (`overscroll-behavior: contain`); CSS "scroll shadows" show on the side with hidden content |
| Sticky footer | footer is outside the scroll area; actions order secondary → primary; `danger` swaps in `btn-danger` |
| Sizes | `dialog_size` sm (30rem, default), md (40rem), lg (56rem), xl (72rem); below 576 px every dialog becomes a full-width bottom sheet with a handle |
| Focus | Alpine `appDialog` traps Tab, focuses the first invalid field, then the first field, then the initial-focus button; focus returns to the opener (or `#main-content`) on close. Hand-written `data-app-dialog` dialogs without Alpine get the same trap/restore/backdrop behaviour from delegated handlers |
| ESC / backdrop | `data-dialog-esc="block"` (`esc_blocked`), `data-dialog-backdrop="static"` (`static_backdrop`, default for `danger`); a press that starts on the dialog and ends on the backdrop never closes it |
| Loading | HTMX forms show `[data-loading]` (button spinner, dimmed body); plain forms get `data-submitting` + `aria-busy` and cannot be submitted twice (cleared on `pageshow` and after 20 s) |
| Server errors | `DialogView.form_invalid` re-renders the dialog in place (HTTP 200) with `form_errors.html` / labelled fields; the first invalid field takes focus; transport failures raise a danger toast |
| Success feedback | a flash message after redirect/refresh (shown as a toast), or - when `refresh_on_success = False` - an immediate success toast through the `toast` `HX-Trigger` event |
| Nested safety | native top layer stacks dialogs; Esc and backdrop act on the top dialog only; focus returns to the lower dialog; the scroll lock stays until the last modal closes; the command palette refuses to open over a modal |
| Scroll lock | `html:has(dialog:modal)` hides page scroll; `scrollbar-gutter: stable` keeps the page from shifting |
| Reduced motion / dark | animations use motion tokens inside the reduced-motion guard; surfaces, borders and overlay use theme tokens |
| Long text | title, message and body use `overflow-wrap: anywhere`; the body scrolls instead of growing |
| Labelled fields | `dialog_form.html` renders every field through `form_field.html` (label, help, errors) |

## Dialog kinds

| Kind | Where | Shell? | Status |
| --- | --- | --- | --- |
| Shared shell (include/extend) | `components/dialog.html`, `confirm_dialog.html`, `dialog_form.html`, `dialog_page.html` | yes | complete; covered by `tests/frontend/test_dialogs_browser.py` |
| JavaScript confirm | `components/dialog_js_template.html` (`Dialogs.confirm`, `data-confirm-dialog`, htmx confirm, unsaved guard) | yes | complete (reason + typed phrase supported) |
| HTMX-loaded `DialogView` | blog (`PostWorkflowDialog`, `PurchasePostDialog`, `CancelGuideRequestDialog`), bounty (`CancelBountyDialog`), devices (`SlotDialog`), promotions (`ClaimDialog`), wallet cancel top-up (`confirm_dialog`) | extends | complete through the shell |
| Hand-written public dialog | `marketplace/dialogs/payout_request.html` (`#payout-dialog`) | partial | **migrate**: no header/close button, no sticky footer, no inline-page fallback structure. Cancel (`data-dialog-close`) now works and the dialog traps/restores focus, but it should extend `components/dialog.html` (`DialogView` for payouts) |
| Device registration prompt | `templates/base.html` (`.device-registration-dialog`) | own markup | works (modal, Esc, focus restore, no-JS fallback); **migrate** to the shell so it shares scroll shadows, sheet and sticky footer |
| Command palette | `components/command_palette.html` | own (`.cmdk`) | complete: modal, Esc layering, focus restore, backdrop close, 360 px layout |
| Staff dialogs (admin shell) | `apps/core/admin_dialogs.py`, `templates/admin/staff_dialog.html` (tags merge/reject, comments moderation, ads place/remove/review) | admin counterpart (`mf-ui-dialog`) | owned by the admin UI kit; **gaps**: no focus trap, no loading state, no scroll edge shadows, no in-place toast. Port the shell's behaviours (`trapTab`, `data-loading`, edge shadows) into the admin counterpart in a later wave |

## Verification (Playwright, `tests/frontend/test_dialogs_browser.py`)

Lifecycle/focus/server validation; done event without reload; confirmations replacing native dialogs; unsaved guard; responsive/dark/reduced motion; no-JS page; long content with sticky footer, scroll lock and no layout shift; size variants; loading state, success toast and focus restore; confirm over a dialog (nested); hand-written dialogs; bottom sheet for the inline, HTMX and JavaScript kinds at 360 px. The palette is covered by `tests/frontend/test_design_system_browser.py`.
