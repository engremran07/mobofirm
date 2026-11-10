# Rich-text editor (Summernote)

Every place where formatted text is edited uses one editor, configured per context from
**System → Editor profiles** in the admin. The server (`nh3`) stays authoritative: the
browser only produces what the server will accept.

## Stack and CSP

- **Library**: Summernote 0.9.1 *lite* (MIT; no Bootstrap JS dependency) with jQuery 3.7.1
  (MIT; jQuery 4 breaks Summernote). Both are bundled by Vite into `editor-core.js` /
  `editor-core.css` and are **never global** (`window.jQuery` stays undefined).
- **Lazy loading**: the widget renders a `<textarea data-mf-editor data-mf-editor-config>`
  plus `<script type="module" src="build/editor.js">`. The 2 KB loader observes those
  textareas with an `IntersectionObserver` and only imports the heavy bundle once one is
  (nearly) visible, so collapsed reply forms, tabs and closed dialogs cost nothing and
  pages without an editor never request it. Fields added later (HTMX, admin "add another")
  are picked up by a `MutationObserver`. `data-mf-editor-when="field=value"` defers an
  editor until another field has that value (a page's HTML format).
- **Strict CSP**: no CDN, no inline script, no inline handlers, no inline styles.
  Summernote's own colour/font tools and its help dialog write or render inline `style`
  attributes, so they are not used: the editor instantiates only the modules the profile
  needs, and its colour, font, size, alignment, emoji and shortcut tools are custom
  (`frontend/src/modules/editor-toolbar.js`) and write curated classes. All dynamic
  styling is done through the CSSOM (allowed by CSP). Verified under the public policy
  (`tests/frontend/test_editor_browser.py`).
- **Theming**: `frontend/src/styles/editor.css` uses the design tokens
  (`--mf-surface-raised`, `--mf-text`, `--mf-border`, `--mf-accent`, …) with literal
  fallbacks so it also works in the Unfold admin (`html.dark`). Logical properties,
  `dir="auto"` on the editing area, wrapping toolbar and 2.5 rem touch targets on phones.
  Content classes (`mf-fg-*`, `mf-bg-*`, `mf-font-*`, `mf-size-*`, `mf-align-*`,
  `mf-table`, `mf-video`) live in `frontend/src/styles/editor-content.css`; the palette
  has separate light and dark values.
- **Accessibility**: the visible `<label>` names the editing area (`aria-labelledby`, a
  click focuses it), the toolbar has `role="toolbar"` and an accessible name, every
  button has an `aria-label`, messages and the character counter are an `aria-live`
  region, `required` is enforced on submit (the hidden textarea cannot take it).

## Profiles

`apps/core/editor` owns the catalogue and the defaults; `site_settings.EditorProfile`
stores one row per context and overrides them (registered through
`register_profile_source`, so `core` never imports `site_settings`).

| Context | Used for |
| --- | --- |
| `blog_post` | Journal posts and article rendering |
| `guide_request` | Guide requests and solutions |
| `comment` | Comments (reduced profile; moderation unchanged) |
| `page` | CMS pages with the HTML format |
| `listing` | Marketplace listing descriptions |
| `firmware_notes` | Firmware release notes and descriptions |
| `announcement` | Announcements and promotion text |
| `request` | Catalogue and firmware requests |
| `admin_default` | Any other admin textarea |

Per profile: master switch (off = plain textarea; content stays valid and is still
sanitised), feature toggles (bold, italic, underline, strike, sub/superscript, fonts,
colours, clear formatting, headings/quotes/code, lists, alignment, rule, tables, links,
images, video, emoji, undo/redo, code view, fullscreen, help), video providers, image
upload (on/off, maximum KiB, per-user daily limit), maximum visible length, spellcheck,
placeholder and height. Saving is validated (uploads need Images; video needs a
provider). **Reset selected to built-in defaults**, enable and disable are bulk actions;
profiles cannot be deleted. `manage.py seed_editor_profiles [--reset]` (also run by
migration `0002_editor_profiles`) creates the missing rows idempotently. The resolved
profile is cached and invalidated on every change.

## Server-side policy

`apps/core/editor/sanitizer.py` derives the `nh3` allow-list from the enabled features:

- tags come from the feature catalogue (`features.py`); `class` values are limited to the
  curated names of the enabled features; no `style`, `id`, `on*`, `srcdoc` or `data:`;
- links keep `http`, `https`, `mailto`, `tel` and always get `rel="nofollow noopener"`;
- images need the Images feature and a same-origin path (`/media/...`);
- iframes need the Video feature and an enabled provider; the source is rewritten to
  `youtube-nocookie.com/embed/<id>` or `player.vimeo.com/video/<id>?dnt=1` and gets
  `sandbox`, `loading="lazy"` and `referrerpolicy`; everything else is dropped;
- empty editor markup (`<p><br></p>`) collapses to an empty string.

**Narrowing happens when input is accepted** (`RichTextField`, `RichTextFormMixin`,
`RichTextAdminMixin`, `clean_rich_text`): forms sanitise with the context's profile and
check the visible-text length. **Storage and rendering use the widest policy**
(`sanitize_stored_html`, `render_rich_text`, the `rich_text` filter): they never depend on
the database or the current admin choice, so a post written with a table keeps its table
after an administrator disables tables, and a model `save()` cannot silently strip
content. Legacy plain text is turned into paragraphs. CMS pages keep their own,
broader allow-list and add the audited video embeds through `video_embed_options`.

`VideoEmbedCspMiddleware` (`video_embed_csp_middleware`) leaves `frame-src 'none'` and
adds only the two player hosts to responses whose HTML contains a sanitised embed or an
editor that may produce one; it merges into the admin or analytics policy.

## Image upload

`POST /site/editor/upload/` (`site_settings:editor_upload`, CSRF-protected, JSON):
signed-in users only, the profile must be enabled with Images and Upload on, staff-only
for page/announcement/other-admin/firmware-notes contexts and blog authors for posts.
Pillow sniffs the real format (JPEG, PNG, WebP, GIF→PNG), rejects other formats, empty,
truncated, oversized (profile KiB) and huge (> 36 MP) files, bakes in the orientation,
re-encodes from pixels (EXIF/GPS/ICC dropped), caps the edge at 2000 px and stores a
random name under `media/editor/YYYY/MM/`. Non-staff users have a rolling 24 h count
(profile) and a 200 MiB total quota. There is **no personal file storage**: images exist
only to be embedded. `cleanup_editor_images` (daily beat task) deletes images older than
24 h that no field listed in `apps/core/editor/references.py` mentions, and fails closed
when a listed field cannot be resolved. **Adding the editor to a new model field means
adding it to that list.**

## Using it

- Django forms: `class MyForm(RichTextFormMixin, forms.ModelForm): rich_text_fields =
  {"body": "blog_post"}` or `RichTextField(profile="comment")`.
- Admin: `class MyAdmin(RichTextAdminMixin, ModelAdmin): rich_text_fields = {...}`
  (optionally `rich_text_when = {"content": ("content_format", "html")}`).
- Templates: `{{ value|rich_text }}` to render, `{% rich_text_field "comment" "body" value
  id="x" rows=3 %}` for forms that are not Django forms.
- Tests that fill an editor field must wait for the editing area: use
  `tests/frontend/editor_support.fill_rich_text`.

## Known limits

Summernote crashes in `insertParagraph` when Enter is pressed at the end of an empty
inline wrapper left behind by some command sequences; the normaliser drops empty inline
elements from the saved value but the editing area itself is Summernote's. The
guide-request, firmware release-note and catalogue-request fields are not wired to the
editor yet (see the wave report).
