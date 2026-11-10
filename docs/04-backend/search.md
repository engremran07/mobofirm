# Central search engine

Owner: `apps.core` (`apps/core/search/`). One engine answers the header search, the `/search/` results page, every type-ahead picker (country, calling code, brand → model → variant) **and every list page** (chipsets, brands, tools, guides, firmware browse, devices of a brand or chipset, topics, the journal, marketplace listings, the credit history and My files). Apps contribute providers; core never imports a domain app.

## HTTP surface

| URL | Name | Purpose |
| --- | --- | --- |
| `GET /search/?q=&kinds=` | `search:results` | Full page grouped by kind; HTMX requests receive only the results fragment. Shows "Did you mean", the request-a-brand/model/variant/chipset/firmware links (`components/request_cta.html`) and the popular blocks when nothing matches. |
| `GET /search/suggest/` | `search:suggest` | Listbox options for `components/combobox.html`, one request per keystroke (150 ms debounce). Parameters: `q`, `kinds` (comma separated), `mode` (`navigate` links grouped by kind with an icon, or `pick` values), `id` (option id prefix), `limit` (max 20), `filter.<name>` (provider narrowing, e.g. `filter.brand`). |

Both are `GET`-only, never cached (`suggest`) and escape every value. Unknown kinds, prefixes, filter names and limits are validated or ignored.

## Providers

`apps/<app>/search_providers.py` is imported by `CoreConfig.ready()` (`autodiscover_modules`). Subclass `QuerySetProvider` (one queryset) or `SearchProvider` (anything else) and call `register(Provider())`.

| Kind | App | Visible to |
| --- | --- | --- |
| `brand`, `model`, `variant`, `chipset`, `tool`, `firmware` | firmwares | Public, active, staff-approved rows only (unapproved firmware is never returned). Chipsets match their aliases and code names too. |
| `firmware_type` | firmwares | Official, engineering, readback, modified, other (opens the browser filtered by that classification). Matches synonyms such as "stock" or "eng". |
| `blog`, `blog_category` | blog | Live posts; hidden when the blog is disabled for the visitor. A gated post's body is never matched. |
| `tag` | tags | Public tags that label a live post. |
| `listing` | marketplace | Approved listings of active, verified sellers. |
| `page` | pages | Published pages. |
| `country`, `calling_code` | core | Pick-only (`navigable = False`): never in header/results search, only when named in `kinds`. Static dataset in `apps/core/geo`. |
| `user` | users | `staff_only`: invisible to everyone else, including through `kinds=user`. |

Provider attributes: `kind`, `label`, `label_plural`, `noun` (lower-case name used in "Did you mean X (noun)?"), `icon`, `priority` (lower first), `staff_only`, `navigable`, `min_length`, `supports_browse`, `popular_title`. `QuerySetProvider` adds `suggestible` (offer the kind in "did you mean") and `watches` (model classes whose saves/deletes refresh the cached vocabulary and popular lists). Registering a different class for an existing kind raises `ValueError`; the same class is idempotent.

## Behaviour

- `search(query, *, kinds=None, limit=10, user=None, filters=None) -> list[SearchHit]`; `search_grouped(...)` for the results page.
- Queries are trimmed, capped at 100 characters and tokenised (accent-folded, at most 6 words). Every word must match some field; numeric-only queries such as `9200` are valid.
- **Synonyms** (`synonyms.py`): vendor abbreviations (`mtk` = MediaTek, `sd` = Snapdragon, `qcom`), SoC part numbers (`MT6985` = Dimensity 9200, `SM8550` = Snapdragon 8 Gen 2, ...) and firmware-type spellings. A synonym match ranks just below the literal word.
- **Ranking**: exact > prefix > word prefix > substring, with a bonus for earlier fields (a name beats a description); ties by provider priority, then title. Deterministic.
- **Typo tolerance** (`fuzzy.py`, `trigram.py`): when a strict pass finds fewer than `FEW_RESULTS` (3) hits, providers that found nothing are asked again with typo tolerance ("snapdargon"). Words of 4+ letters tolerate 1 edit (7+ letters: 2); words containing digits and words shorter than 4 characters must match exactly, so `9200` never becomes `9300` and `S23` never becomes `S24`. On PostgreSQL with `pg_trgm` installed the database shortlists the rows (`TrigramSimilarity`); everywhere else, including the SQLite test database, pure Python does the matching. Typo hits rank below every strict hit.
- A query shorter than a provider's `min_length` skips that provider. An empty query returns a browse list only when `kinds` names providers with `supports_browse` (pickers), never for header search.
- A provider that raises `DatabaseError` is logged and skipped; the rest still answer.
- `SearchHit.url` is a same-site path (empty for pick-only kinds). Templates render links only for paths starting with a single `/`.

## Did you mean

`did_you_mean(query, user=None, kinds=None, exclude_urls=())` returns the closest brand, model, variant, chipset, tool, tag, category or firmware type, labelled with its entity type (`Samsung (brand)`, `Engineering (firmware type)`). Only near misses are offered (never an exact name, never a different number); staff-only and unpublished rows are never suggested. Each provider publishes a bounded public vocabulary that is cached for 10 minutes and refreshed when a watched model changes (`vocabulary.py`).

It appears in the header combobox (as options in a "Did you mean" group), on `/search/`, and on every list page through `components/did_you_mean.html` when a list has fewer than three rows. The request-a-... buttons stay available in every empty state.

## Lists: one engine for every page

List views call the engine instead of writing `icontains` filters:

```python
result = search_queryset(
    Chipset.objects.all(),
    request.GET.get("q"),
    CHIPSET_FIELDS,
    annotations=CHIPSET_ANNOTATIONS,
)  # QuerySet
outcome = search_querysets(
    {"official": qs1, "engineering": qs2}, q, FIRMWARE_FIELDS
)  # several tables
found = search_items(entries, q, lambda entry: entry.search_fields)  # in-memory rows
context.update(
    list_search_context(result, total, user=request.user, kinds=("chipset", "brand"))
)
```

`search_queryset` requires every word (or a synonym) to appear in some field, then falls back to the typo-tolerant pass and reports the corrected spelling (`search_corrected`). Annotations add computed text columns (aliases JSON cast to text, a human reason label, a "free body only" column for blog posts).

Templates: `components/search_box.html` (input + indicator, per keystroke HTMX with a 250 ms debounce, `hx-push-url`, plain GET fallback), `components/search_feedback.html` (count in an `aria-live` container, corrected-spelling note, did-you-mean) and a results fragment returned for `HX-Request` (views are `@vary_on_headers("HX-Request")`). Other filters (vendor, brand, type, tier, ...) are included in the request with `hx-include="closest form"`, so they survive typing.

| Surface | View | Fields |
| --- | --- | --- |
| Chipsets `/firmwares/chipsets/?q=&vendor=` | `chipset_list` | name, vendor, family, slug, aliases (code names) |
| Firmware browse | `firmware_browse` | file name, build, Android, chipset, brand, model, variant (all classifications) |
| Device search `/firmwares/search/` | `universal_search` | model name, marketing name, codes, codename, aliases, chipset, brand |
| Brands, brand hub | `brand_list`, `library_hub` | name, slug |
| Devices of a brand / chipset | `brand_detail`, `chipset_detail` | model fields |
| Flashing tools, guides | `flashing_tools`, `flashing_guides` | tool name/category/version; guide title, brand, device |
| Topics (tags) | `tags:list`, `tags:search` | name, normalised name, synonyms |
| Blog journal | `blog:post_list` | title, summary, category, brand/model and the **public** body only |
| Marketplace | `listing_browse`, seller pickers | title, description, seller |
| Credit history | `wallet:ledger` | note, reference, event key, reason label |
| My files | `library:my_files` | file, brand, device, variant, classification |
| Legacy JSON | `firmwares:api_search_autocomplete`, `tags:search`, blog similar posts | via the same engine |

## Popular blocks

"Popular models", "Popular firmware" and "Popular articles" come from `SearchProvider.popular()` through `popular_entries(kind, scope=...)`:

- Firmware score = 5 × completed downloads (`DownloadToken` used) + page views (`FirmwareView`) inside the window; only active, staff-approved files are counted (the scoring queries are restricted to the public catalogue). A model scores the sum of its files. Articles rank by total reads.
- Staff configure the window, list size, cache time and an on/off switch under **Admin → System → Search and popularity** (`core.SearchSettings`, defaults 30 days / 6 entries / 10 minutes).
- Results are cached per (kind, scope, window, size). Saving the settings, or saving/deleting a watched model (approving a file, deactivating a model, ...), invalidates them immediately; otherwise they expire after the configured minutes.
- Cost is constant: two grouped queries per classification plus one fetch of the winners; no per-row queries.
- `{% load search_tags %}{% popular_blocks "model,firmware,blog" brand=slug chipset=slug heading="..." layout="row|stack" %}` renders them (nothing when empty or switched off). Used on the library hub, firmware browse, brand and chipset pages, the journal sidebar and the empty `/search/` state.

## Combobox

`components/combobox.html` + `frontend/src/modules/combobox.js` (Alpine CSP build + HTMX). Navigation mode adds:

- options grouped by kind, each group labelled with its icon (`role="group"`), plus a "Did you mean" group and the request buttons;
- **recent searches**: the last six submitted queries are kept in `localStorage` only (key `mf:recent-searches`), shown when the empty field is focused. "Clear recent searches" and "Turn off recent searches" are options in the list (opt-out key `mf:recent-searches:off`; `/search/` shows a toggle to turn them back on). A single combobox can opt out with `recent="off"` (`data-recent="false"`);
- the **`/` shortcut** (and Ctrl/Cmd+K) focuses the header search unless the visitor is typing in a field (`modules/search.js`, `data-global-search` on the header form);
- keyboard: arrows, Home/End, Enter, Escape, Tab (ARIA 1.2 combobox).

## Pickers

`mode=pick` options carry `data-value`, `data-label`, `data-kind` and provider `extra` pairs as `data-*` attributes (countries expose `data-calling-code`). Cascades send `filter.<param>` from the parent combobox. See "Combobox contract" in `docs/05-frontend/components.md`. The marketplace speciality pickers use the brand and chipset providers.

## Tests

`tests/backend/core/test_search_engine.py` (registry, providers, permissions, ranking, XSS, endpoints), `test_search_text.py` (normalisation, synonyms, ranking, typo helpers), `test_search_listing.py` (chipset `?q=9200`, aliases, typos, did-you-mean, HTMX fragments, trigram query), `test_search_surfaces.py` (tags, journal, marketplace, ledger, My files), `test_search_suggest.py` (combobox partial), `test_search_popular.py` (analytics, window, scope, cache, safety), `tests/backend/firmwares/test_list_search.py` (every firmware list) and `tests/frontend/test_search_browser.py` (keystroke suggestions, live filtering, did-you-mean click, `/` shortcut, recent searches, console/CSP, responsive overflow).
