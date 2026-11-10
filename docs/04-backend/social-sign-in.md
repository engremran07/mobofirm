# Social sign-in (Google and other providers)

The login and sign-up pages **always** list every supported provider ("Continue with Google"). Whether the button can start an OAuth flow is decided in one place, `apps/users/services/social_providers.py`, which asks allauth the same question its login view asks (`adapter.list_apps(request, provider=…)`): is there an app for this provider on the current site with a real client id **and** secret?

| State | Button behaviour |
| --- | --- |
| Configured | A CSRF-protected POST to `/accounts/<provider>/login/?process=login` (carries a safe `next`), which continues into the provider's OAuth consent screen. |
| Not configured | A `type="button"` that opens an app-owned dialog: "<Provider> sign-in is not set up yet by the site admin." Nothing is submitted; no browser `alert`. |

Placeholder values (`your-…`, `placeholder…`, `changeme…`) count as not configured, in the buttons and in the admin Status column.

## Where an admin configures a provider

Two sources feed allauth; both work and both are read by the same helper:

1. **Admin panel (preferred, no deploy)**: Admin → *Social applications* (`/admin/socialaccount/socialapp/`). Pick the provider, enter **Client id** and **Secret**, keep the current site selected, save. The form pre-selects the current site (`SITE_ID`) and attaches it automatically if none is chosen; it shows the exact **Redirect URI** to register with the provider.
2. **Environment**: `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` (both or neither; `app/settings/base.py` refuses a half-set pair) populate `SOCIALACCOUNT_PROVIDERS["google"]["APP"]`.

If both exist for the same provider, the admin-saved row is used (the admin shows a warning when saving). `manage.py setup_social_providers` prints the current status.

## Getting Google keys

1. Open <https://console.cloud.google.com/apis/credentials> and select (or create) a project.
2. Configure the OAuth consent screen (app name, support email, authorised domain).
3. **Create credentials → OAuth client ID → Web application**.
4. Under **Authorised redirect URIs** add `https://<your-domain>/accounts/google/login/callback/` (the admin form displays it for the current site).
5. Copy the **Client ID** and **Client secret** into the Social application form (or the two environment variables) and save.
6. Check: the login page's Google button now continues to Google instead of opening the notice.

`SITE_ID` must name an existing `django.contrib.sites` row whose domain matches the public host; the social application must be attached to that site.

## Adding another provider

The pages list every allauth provider that is installed in `INSTALLED_APPS` (today: Google). To offer another one, add its `allauth.socialaccount.providers.<name>` app, allow its authorisation origin in the CSP `form-action` list (`app/settings/security.py`, today only `https://accounts.google.com`), and configure it as above. This is a deliberate product/security decision, not an automatic one.
