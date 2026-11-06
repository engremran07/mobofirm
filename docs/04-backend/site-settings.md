# Site Settings (`apps.site_settings`)

`site_settings` is the one runtime, admin-editable settings authority for site-wide values. Django/environment settings (`app/settings/base.py`, `.env`) are the defaults and bootstrap values; the database row only holds deliberate overrides.

**Inheritance rule.** A blank text value, or a `NULL` number/choice/boolean, means "use the environment default". `0` and `False` are real overrides.

## Reading settings

| Need | Use |
| --- | --- |
| Any non-secret value | `apps.site_settings.services.get_site_settings()` returns an immutable, typed `SiteSettingsSnapshot` (cached 5 minutes under a versioned key; saving or deleting any settings model, or overriding a watched Django setting, rotates the version, and the cache is also cleared on commit). |
| Templates | `site_settings` (lazy snapshot) from `apps.site_settings.context_processors.site_settings`. `app.context_processors.site_chrome` only adds navigation and ecosystem links and takes `site_name` from the same snapshot. |
| Absolute URLs | `site_origin(request)` / `absolute_url(path, request)`: the configured site URL wins, `force_https` upgrades the request scheme, protocol-relative and foreign paths are refused. |
| Mail credentials, reCAPTCHA private key | `get_email_runtime_config()`, `get_recaptcha_secret()`. Decrypted on demand, never cached, never part of the snapshot, `/site/info/`, logs or the admin. |
| Invalidate by hand | `manage.py clear_site_settings_cache` or `clear_site_settings_cache()`. |

If the table is missing (before the first migration) the accessor logs a warning and serves the environment defaults without caching.

## Field-to-owner mapping

Every setting has exactly one owner.

| Setting | Owner | Environment default |
| --- | --- | --- |
| Name, header, description, logo, dark logo, favicon, accent colours | `site_settings` | `SITE_NAME`, `SITE_DESCRIPTION`, `SITE_LOGO_URL`, `SITE_DARK_LOGO_URL`, `SITE_FAVICON_URL` |
| Site URL, contact e-mail, copyright, social links, share image, Twitter handle | `site_settings` | `SITE_URL`, `SITE_CONTACT_EMAIL` |
| Default language, time zone | `site_settings` | `LANGUAGE_CODE`, `TIME_ZONE` |
| Maintenance mode and message, force HTTPS | `site_settings` (environment `MAINTENANCE_MODE=true` / `FORCE_HTTPS=true` force them on) | `MAINTENANCE_MODE`, `FORCE_HTTPS` |
| E-mail mode, sender, Gmail address and app password, SMTP host/port/user/password/TLS/SSL | `site_settings` (passwords encrypted with `SOCIAL_CREDENTIAL_ENCRYPTION_KEY`, write-only); transport built by `apps.core.email_backends.runtime.RuntimeEmailBackend` | `GMAIL_*`, `DEFAULT_FROM_EMAIL`, `MAILERS` options |
| reCAPTCHA mode, site key, private key, score, timeout, expected host | `site_settings` (private key encrypted); verification in `apps.users.services.recaptcha` | `RECAPTCHA_*` |
| Profile-completion requirement, e-mail verification code length/type | `site_settings` | `REQUIRE_PROFILE_COMPLETION` |
| Firmware download gate (countdown, ad gate, token expiry, login, per-day cap, hotlink, link encryption/TTL) | `site_settings`; consumed by `apps.firmwares.download_service` | `FIRMWARE_DOWNLOAD_*` |
| GA4 / GTM IDs, verification meta tags and files | `site_settings` | none (off until set) |
| Sign-up, password reset, notifications, MFA requirement and issuer, login-attempt limit and window | `users.UsersSettings` (`ENABLE_SIGNUP` / `REQUIRE_MFA` in the environment stay a hard floor that is OR-ed in) | `ENABLE_SIGNUP`, `REQUIRE_MFA` |
| Device limits, fingerprint and registration policy | `devices.DeviceConfig` | none |
| Sitemap, robots, redirects, SEO site-name and `twitter:site` overrides, default social image | `seo.SEOSettings` (blank values fall back to `site_settings` name, handle and share image) | none |
| Feature flags for pages, shop, marketplace, filemanager | `core.AppRegistry` | none |
| Ads, blog, comments behaviour | their own apps | none |

Dropped from the pasted code: wallet/credit/bounty/economy fields, a second `AppRegistry` model, and references to `themes`, `admin_suite` and `import_export`.

## Behaviour

- **Admin** (`apps/site_settings/admin.py`, Unfold, built on `apps.core.admin_base`): tabbed singleton form (Branding, Site and contact, Social, SEO defaults, Localization, Operations, Email, Security, Captcha, Download gate, Analytics) with verification tags/files as inline tabs and as their own lists. Secret inputs are write-only: an empty input keeps the stored value, a typed value replaces it, a "remove" checkbox clears it, and the stored value is never rendered. The "Save and send test email" action mails the signed-in staff member and reports only the exception class. Saves write an audit log line (`apps.site_settings.audit`) with changed field names and secret names, never values.
- **Maintenance** (`apps.core.middleware.maintenance.MaintenanceModeMiddleware`): anonymous visitors get a 503 page with `Retry-After`; staff, `/health/`, `/admin/`, sign-in, `/static/`, `/media/` and `/site/` stay reachable.
- **Locale** (`SiteLocaleMiddleware`): the configured language applies only when the visitor sends neither a language cookie nor `Accept-Language`; the configured time zone is activated per request.
- **Analytics**: `templates/site_settings/head.html` emits only a same-origin `<script src>` with data attributes when an ID is set; `static/site_settings/analytics.js` loads gtag/GTM, honours Do Not Track and Global Privacy Control. `SiteAnalyticsCspMiddleware` adds the Google hosts to `script-src`, `img-src` and `connect-src` for public HTML responses only while an ID is configured, never `unsafe-inline`, and never touches the admin or non-HTML responses.
- **Theme**: `/site/theme.css` overrides the light-theme accent tokens only when colours are set (contrast against white of at least 4.5:1 is enforced) and swaps the logo for the dark theme.
- **Public endpoints**: `/site/info/` (non-sensitive JSON), `/site/verification/<name>/` and, for `.html`/`.xml`/`.json`, `/<name>` at the site root. Only active, allow-listed, extension-checked files are served, as `GET`/`HEAD`, with `default-src 'none'; sandbox`; anything else is a normal 404 and there are no redirects. Legacy `/privacy/`, `/terms/`, `/cookies/` permanently redirect to the published CMS page of that slug (`cookies` also accepts `consent`) or return 404.

## Verification

`tests/backend/site_settings/` covers the accessor and cache invalidation, environment fallbacks, validation, secret encryption and write-only handling, the admin pages and test-email action, runtime email backend selection, verification-file serving, `/site/info/`, maintenance, locale, analytics CSP/markup, legacy redirects, and the SEO/users/firmware integrations.
