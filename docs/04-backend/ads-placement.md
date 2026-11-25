# Ad placement: zones, placement manager, test profile

Owner: `apps.ads`. Public pages declare **zones**; placements decide what
appears in each zone on each template. Template source is never rewritten at
runtime.

## Zone vocabulary (`apps/ads/zones.py`)

| Zone | Where it sits | Default size |
| --- | --- | --- |
| `header` | under navigation, above page content | 728x90 |
| `sidebar` | beside the main column | 300x250 |
| `in_content` | inside an article or detail page | 300x250 |
| `after_content` | after main content, before the footer | 728x90 |
| `between_items` | between items in a list or grid | 300x250 |
| `left_rail`, `right_rail` | desktop side rails on model and firmware download pages | 160x600 |
| `footer` | directly above the site footer | 728x90 |

A page declares a zone with one generic tag:

```django
{% load ad_tags %}
{% ad_zone "after_content" %}
```

`templates/base.html` declares `header`, `after_content` and `footer` for every
page. Detail and list templates add `sidebar`, `in_content` and `between_items`
(firmware detail, blog detail and list, brand and model pages, firmware browse,
search results, shop pricing). Account, allauth, error, admin and consent
templates never show ads.

## What a zone renders

`{% ad_zone %}` calls `apps.ads.services.zones.resolve_zone`. It renders nothing
and takes no space unless every condition holds, in this order:

1. The template is not on the never-list or staff exclusion list
   (`TemplateAdExclusion`).
2. A placement exists for that template and zone. A template-specific placement
   beats a site-wide one; site-wide placements never fill `users/`, `wallet/`,
   `referral/`, `devices/` or `shop/` templates.
3. The placement is switched on and active.
4. Ads are enabled for the user and URL (`AdsSettings.should_show_ads`), and the
   visitor allowed the **advertising** consent category.
5. The device matches the placement (`all`, `desktop` or `mobile`).
6. A creative is eligible: assigned and enabled, campaign live (schedule and
   linked promotion), within daily and lifetime caps, and of a type allowed by
   the placement. Weighted rotation picks one; otherwise the fallback creative
   is tried.
7. The creative markup passes the allowlist validator
   (`services/markup.py`).

The output is an `<aside aria-label="Advertisement">` with a visible label and a
reserved-size class (`ad-zone--s728x90`, etc.; `frontend/src/styles/ads.css`)
so loading an ad never shifts the layout. Banner links go through
`/ads/go/<placement>/<creative>/`, which records the click (consent permitting)
and redirects only to the stored advertiser URL. An impression is recorded at
render time, only with consent.

Ads analytics retain keyed IP and User-Agent digests only. Raw IP addresses,
User-Agent strings and session keys are not stored.

## Placement manager (staff)

`/ads/manage/placements/` requires `ads.change_adplacement` and uses the staff
console shell. It lists each scanned template with its type, traffic estimate,
score, scanner rationale, declared zones, placements and best scan candidate.

- **Scan templates** (POST + redirect) runs the scanner. Paths use forward
  slashes so they match Django template names. It counts explicit `{% ad_zone %}`,
  `{% ad_article_body %}` and `{% ad_tile %}` slots and reports declared left/right
  rails separately from content-based recommendations.
- **Place ad here** opens an app-owned dialog for zone, sizes, devices and an
  optional fallback creative. It can link an existing code-addressed placement
  and switch it on. New placements start **switched off** and serve nothing
  until a creative is assigned and the placement is enabled.
- **Site-wide zones** place an ad on a shared-layout zone for every page.
-   Firmware model and download pages declare desktop-only left and right rails;
  staff can place inventory on those zones without changing the page flow.
- A placement can be switched on or off, or removed with a confirmation dialog.
  Removal is a soft delete.
- `/ads/manage/suggestions/` is the discovery queue. Suggestions can be
  approved or rejected individually or in bulk. Approval rechecks that the
  template still declares the zone and is not excluded. Suggestions mapping to
  the same template zone share one placement.

## Test profile

Staff holding `ads.change_adplacement` can preview every declared zone as a
marked box ("TEST AD - 728x90 - zone header - placement #12", "PAUSED ZONE"
for a switched-off placement, or "EMPTY ZONE" when nothing is placed). It
ignores the master switch and consent, never records impressions or clicks, and
is invisible to everyone else.

- Session toggle: POST `/ads/manage/test-profile/` from the dashboard or
  placement manager. It is valid for one hour.
- Per-template **Preview with test ads** opens the real public route with
  `?ad_preview=<signed token>`. The token is signed, bound to the staff user and
  valid for 10 minutes. A plain `?ad_preview=1` or a token issued to someone
  else does nothing. Templates needing an object have no static route; switch
  on the session toggle and open a page that uses them.

`manage.py seed_ad_test_profile` (idempotent) creates a paused house campaign,
HTML house creatives and switched-off placements for the main zones, including
the model and download side rails. It never touches the master switch. Turn on
the test profile, or deliberately activate the campaign and placements, to see
it.

## Serving checklist (`/ads/dashboard/`)

The dashboard shows why nothing renders: ads switched on, templates scanned,
zones placed, creatives assigned, performance, serving controls, test profile,
rewarded ads status, placements, campaigns and creatives. Visitors must also
allow advertising cookies.

Dashboard changes require the matching Django model permission:
`ads.change_adssettings`, `ads.change_adplacement` or `ads.change_campaign`.
Staff status alone does not authorize changes. The public JSON fill endpoint
also checks user ad eligibility and template exclusions, and returns validated
creative markup only.

Rewarded ads also power file unlocks in the pricing ladder (reward type
`download_unlock`; see `rewarded-ads.md`). An over-allowance account watches
rewarded ads, counted by a shop ticket, to unlock one free or featured file.
Packages with `ad_free` never see download-gate ads or unlock offers.
`direct_links` (not `ad_free`) removes the URL shortener.

## Automatic slots: in-article paragraphs and in-feed tiles

Two zones are detected from content instead of hand-placed. They are ordinary
zones with the same switch, consent, device, caps, rotation, fallback, deny
lists, test profile and impression/click tracking. Only their position is
automatic.

- **`in_article`**: `{% ad_article_body article.html %}` places an ad between
  text paragraphs in a blog post.
- **`between_items`**: `{% ad_tile ... %}` places a sponsored tile after the
  configured tile interval on brand, model, chipset, tag, firmware, guide and
  blog grids.

The placement manager detects both tags through
`placement_manager.AUTO_TAGS`, so **Place ad here** offers the zone on
`blog/post_detail.html`, journal and article hubs, firmware brand and chipset
lists, brand and model details, guide listings, browse, library and tag pages.
The tile tag takes the canonical page template
(`page="firmwares/brand_list.html"`), so full pages and HTMX fragments resolve
to the same placement. Switching that placement off (or removing it) turns
automatic ads off for that listing kind. A site-wide `between_items` placement
fills every listing that declares the zone.

**Spacing** (`AdPlacement.auto_first_after`, `auto_every`, `auto_max` and
`auto_min_items`) is edited on the placement's *Automatic spacing* tab. A value
of `0` uses the default (see `services/auto_slots.py`):

| Content | First after | Every | At most | Minimum |
| --- | --- | --- | --- | --- |
| Article paragraphs | 3 | 4 | 3 | 5 paragraphs |
| Grid tiles | 8 | 8 | 2 | 9 tiles |

Hard rules no setting can lower: the first slot is after paragraph 2 at the
earliest, slots are at least 2 apart, and an ad is never the last tile (a real
tile always follows it). Articles require at least
`AdsSettings.min_content_between_ads` words between ads. Page-wide automatic
slots also stop at `AdsSettings.max_ads_per_page`; one creative is never shown
twice on a page (`resolve_zone(..., unique=True)` excludes creatives already
shown). Counts are per rendered page, so each paginated list page is counted
separately.

**Articles.** Sanitised article HTML (`blog.rendering.render_article`, never
cached with ads) is scanned with `html.parser` for top-level blocks. The slot is
spliced at an exact source offset after a text paragraph whose next block is
another text paragraph. Ads are never inserted inside lists, tables, figures,
quotes or code blocks; next to a heading; between a paragraph and its
introductory list; or in the first paragraph. Text outside a block, empty or
image-only paragraphs and scripts are not paragraphs. Unbalanced markup gets
no ads and is returned untouched. Locked (paywalled) teasers carry none.
Markup uses the usual labelled `<aside>`, a reserved size class, safe link
relations and lazy images.

**Tiles.** `templates/ads/includes/tile_ad.html` renders the sponsored item
with its sibling's classes (`tile="card-ui--tile card-ui--brand-tile"`,
`layout` = `tile`, `card` or `tag`). Size, padding, radius and elevation come
from the same CSS; the grid (`align-items: stretch`) gives it the row height.
`.card-ui--sponsored` only changes the border colour and adds the "Sponsored"
label (`tests/backend/ads/test_auto_slots_render.py` fails if a sponsored
selector sets a box property). Banner creatives show their image, headline and
tagline (never the internal name; without a headline, `Visit <host>`). Native,
HTML and script creatives are shown inside the tile, clipped to its size.

Nothing is inserted on error, sign-in, consent, account or checkout pages; for
ad-free users (package or rewarded); for excluded roles; without advertising
consent; or when the listing is empty.

Migration: `ads/0010_auto_slots_and_tile_text` (additive: four spacing columns,
creative headline/tagline and the zone choice).

## Tests

`tests/backend/ads/test_zones.py` covers switch, consent, placement, exclusion,
device, assignment, caps, rotation, fallback, click redirect and test-profile
isolation. `test_placement_manager.py` covers manager workflows;
`test_seed_ad_test_profile.py` covers seeding; `test_auto_slots.py` covers
spacing and HTML parsing; `test_auto_slots_render.py` covers tags, suppression,
dedupe, real listing pages and CSS contracts. Playwright checks in
`tests/frontend/test_ads_manager_browser.py` and
`tests/frontend/test_ads_inline_browser.py` verify tile sizing and in-article
placement at 360/768/1280 widths, with no console errors, overflow or layout
shift.
