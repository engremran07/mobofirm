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

`/ads/manage/placements/` (permission `ads.change_adplacement`, staff console shell). It lists
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

Rewarded ads also power the file unlocks of the pricing ladder (reward type
`download_unlock`, see `rewarded-ads.md`): an over-allowance account watches N rewarded
ads, counted by a shop ticket, to unlock one free or featured file. Packages with
`ad_free` never see download-gate ads or unlock offers, and `direct_links` (not `ad_free`)
is what removes the URL shortener.

## Automatic slots: in-article paragraphs and in-feed tiles

Two zones are *detected from the content* instead of hand-placed. They are ordinary zones with
ordinary placements (same switch, consent, device, caps, rotation, fallback, deny lists, test
profile, impression and click tracking); only the **position** is automatic.

| Zone | Declared by | Where it goes |
| --- | --- | --- |
| `in_article` | `{% ad_article_body article.html %}` (blog post body) | between two text paragraphs |
| `between_items` | `{% ad_tile forloop.counter items\|length ... %}` (brand, model, chipset, tag and firmware grids) | a sponsored tile after tile K, N, ... |

The placement manager detects both tags like `{% ad_zone %}` (`placement_manager.AUTO_TAGS`), so
**Place ad here** offers the zone on `blog/post_detail.html`, `firmwares/brand_list.html`,
`chipset_list.html`, `brand_detail.html` (also the chipset page's model grid), `browse.html`,
`library_hub.html` and `tags/list.html`. The tile tag takes the canonical page template
(`page="firmwares/brand_list.html"`), so a full page and its HTMX fragment resolve to the same
placement. Switching that placement off (or removing it) turns the automatic ads off for that
listing kind; a site-wide placement on `between_items` fills every listing that declares it.

**Spacing** (`AdPlacement.auto_first_after`, `auto_every`, `auto_max`, `auto_min_items`, edited on
the placement's *Automatic spacing* tab; `0` = default, see `services/auto_slots.py`):

| | first after | every | at most | needs at least |
| --- | --- | --- | --- | --- |
| article (paragraphs) | 3 | 4 | 3 | 5 paragraphs |
| grid (tiles) | 8 | 8 | 2 | 9 tiles |

Hard rules no setting can lower: first slot after paragraph 2 at the earliest, slots at least 2
apart, never the last tile (a real tile always follows the ad), and for articles at least
`AdsSettings.min_content_between_ads` words between two ads. Page-wide, automatic slots also stop
at `AdsSettings.max_ads_per_page`, and one creative is never shown twice on one page
(`resolve_zone(..., unique=True)` excludes already shown creatives; no impression is double
counted). Counts are per rendered page: on paginated lists each page is counted on its own.

**Articles.** The sanitised article HTML (`blog.rendering.render_article`, which is never cached
with ads) is scanned with `html.parser` for top-level blocks and the slot is spliced in at an exact
source offset after a text paragraph whose next block is another text paragraph. So never inside
a list, table, figure, quote or code block, never next to a heading, never between a paragraph
and the list it introduces, never in the first paragraph; text outside any block, empty or
image-only paragraphs and scripts are not paragraphs. Markup that does not parse cleanly
(unbalanced tags) gets no ads and is returned untouched. Locked (paywalled) teasers carry none.
Markup: the usual `<aside aria-label="Advertisement">` with a visible label, a reserved size class
(no layout shift), `rel="noopener sponsored nofollow"` and lazy images.

**Tiles.** `templates/ads/includes/tile_ad.html` renders the sponsored item with the sibling's own
classes (`tile="card-ui--tile card-ui--brand-tile"`, `layout` = `tile`, `card` or `tag`), so size,
padding, radius and elevation come from the same CSS and the grid (`align-items: stretch`) gives it
the row height; `.card-ui--sponsored` only changes the border colour and adds the "Sponsored"
label (`tests/backend/ads/test_auto_slots_render.py` fails if a sponsored selector sets any box
property). Banner creatives show their image, `AdCreative.headline` and `tagline` (no internal
name is ever shown; without a headline: "Visit <host>"); native, HTML and script creatives are
shown inside the tile, clipped to its size.

Nothing is inserted on error, sign-in, consent or account/checkout pages, for ad-free (package or
rewarded) users, excluded roles, without advertising consent, or when the listing is empty.

Migration: `ads/0010_auto_slots_and_tile_text` (additive: the four spacing columns, creative
headline/tagline, the zone choice).

## Tests

`tests/backend/ads/test_zones.py` (each hop: switch, consent, placement, exclusion, device,
assignment, caps, rotation, fallback, click redirect, test profile isolation),
`test_placement_manager.py`, `test_seed_ad_test_profile.py`, `test_auto_slots.py` (spacing rules and
the HTML scanner on tricky markup), `test_auto_slots_render.py` (tags, suppression, dedupe, tiles on
real listing pages, CSS contract), and the Playwright checks
`tests/frontend/test_ads_manager_browser.py` and `tests/frontend/test_ads_inline_browser.py`
(sponsored tile box equals its siblings and in-article slots sit between paragraphs at 360/768/1280
with no console errors, overflow or layout shift).
