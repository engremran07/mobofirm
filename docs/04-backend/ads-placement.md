# Ad placement: zones, the placement manager and the test profile

Owner: `apps.ads`. Public pages never name individual ad placements. They declare **zones**;
placements are data that decide which zone shows what on which template. Template source is
never rewritten at runtime.

## Zone vocabulary (`apps/ads/zones.py`)

| Zone | Where it sits | Default size |
| --- | --- | --- |
| `header` | under the site navigation, above the page content | 728x90 |
| `sidebar` | in a sidebar / beside the main column | 300x250 |
| `in_content` | inside an article or detail page | 300x250 |
| `after_content` | after the main content, before the footer | 728x90 |
| `between_items` | between / after the items of a list or grid | 300x250 |
| `footer` | directly above the site footer | 728x90 |

A page declares a zone with one generic tag:

```django
{% load ad_tags %}
{% ad_zone "after_content" %}
```

`templates/base.html` declares `header`, `after_content` and `footer` for every page. Detail
and list templates add `sidebar`, `in_content` and `between_items` (firmware detail, blog
detail and list, brand and model pages, firmware browse, search results, shop pricing).
Account, allauth, error, admin and consent templates never show ads.

## What a zone renders

`{% ad_zone %}` calls `apps.ads.services.zones.resolve_zone`, which says no (renders **nothing**
and takes no space) unless every condition holds, in this order:

1. the template is not on the never-list or the staff exclusion list (`TemplateAdExclusion`);
2. a placement exists for that template and zone (a template-specific placement beats a
   site-wide one; site-wide placements never fill `users/`, `wallet/`, `referral/`,
   `devices/` or `shop/` templates);
3. the placement is switched on and active;
4. ads are enabled for this user and URL (`AdsSettings.should_show_ads`) and the visitor
   allowed the **advertising** consent category;
5. the device matches the placement (`all`, `desktop` or `mobile`);
6. a creative is eligible: assigned and enabled, campaign live (schedule, linked promotion),
   within the campaign's daily and lifetime caps, creative type allowed by the placement;
   weighted rotation picks one; otherwise the placement's fallback creative is tried;
7. the creative markup passes the allowlist validator (`services/markup.py`).

The output is an `<aside aria-label="Advertisement">` with a visible "Advertisement" label and
a reserved-size class (`ad-zone--s728x90`, ...; `frontend/src/styles/ads.css`) so loading an ad
never shifts the layout. Banner links go through `/ads/go/<placement>/<creative>/`, which
records the click (consent permitting) and redirects to the stored advertiser URL only. An
impression is recorded at render time, only with consent.

## Placement manager (staff)

`/ads/manage/placements/` (permission `ads.change_adplacement`, Unfold admin shell). It lists
every scanned template with type, traffic estimate, score, the scanner's rationale, the zones
the template (and the layouts it extends) really declares, its current placements and the best
scan candidate.

- **Scan templates** (POST + redirect) runs the scanner; paths are stored with forward slashes
  so they match Django template names.
- **Place ad here** opens an app-owned dialog: zone (only declared zones), sizes, devices,
  optional fallback creative, optionally link an existing code-addressed placement, switch on
  now. New placements start **switched off** and serve nothing until a creative is assigned
  (placement admin inline) and the placement is on.
- **Site-wide zones** place an ad on a zone of the shared layout for every page.
- Switch a placement on/off, or remove it (confirmation dialog, soft delete).
- `/ads/manage/suggestions/` is the discovery queue: approve/reject one or many (dialogs).
  Approving creates the placement with its zone; two suggestions mapping to the same zone of
  a template share one placement.

## Test profile

Staff holding `ads.change_adplacement` get a preview that forces every declared zone to render
a marked box ("TEST AD - 728x90 - zone header - placement #12", "PAUSED ZONE" for a switched-off
placement, "EMPTY ZONE" where nothing is placed). It ignores the master switch and consent, never
records an impression or click, and is invisible to everyone else (permission checked first).

- Session toggle: POST `/ads/manage/test-profile/` (dashboard or placement manager), valid for one hour.
- Per-template **Preview with test ads**: opens the real public route with `?ad_preview=<signed token>`;
  the token is signed, bound to the staff user and valid for 10 minutes. A plain `?ad_preview=1`
  or a token issued to someone else does nothing. Templates that need an object (a detail page)
  have no static route: switch the session toggle on and open any page that uses them.

`manage.py seed_ad_test_profile` (idempotent) creates a paused house campaign, two HTML house
creatives and switched-off placements for the main zones. It never touches the master switch.
To see it: turn the test profile on, or deliberately activate the campaign and the placements.

## Serving checklist (`/ads/dashboard/`)

The dashboard shows why nothing renders: ads switched on, templates scanned, zones placed,
creatives assigned; plus performance, serving controls, the test profile, rewarded ads status,
placements, campaigns and creatives. Visitors must also allow advertising cookies.

## Tests

`tests/backend/ads/test_zones.py` (each hop: switch, consent, placement, exclusion, device,
assignment, caps, rotation, fallback, click redirect, test profile isolation),
`test_placement_manager.py`, `test_seed_ad_test_profile.py`, and the Playwright check
`tests/frontend/test_ads_manager_browser.py`.
