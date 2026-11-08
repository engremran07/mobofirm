# Central search engine

Owner: `apps.core` (`apps/core/search/`). One engine answers the header search, the `/search/` results page and every type-ahead picker (country, calling code, brand → model → variant). Apps contribute providers; core never imports a domain app.

## HTTP surface

| URL | Name | Purpose |
| --- | --- | --- |
| `GET /search/?q=&kinds=` | `search:results` | Full page grouped by kind; HTMX requests receive only the results fragment. Shows request-a-brand/model/variant/chipset/firmware links (`components/request_cta.html`) when nothing matches. |
| `GET /search/suggest/` | `search:suggest` | Listbox options for `components/combobox.html`, one request per keystroke (150 ms debounce). Parameters: `q`, `kinds` (comma separated), `mode` (`navigate` links or `pick` values), `id` (option id prefix), `limit` (max 20), `filter.<name>` (provider narrowing, e.g. `filter.brand`). |

Both are `GET`-only, never cached (`suggest`) and escape every value. Unknown kinds, prefixes, filter names and limits are validated or ignored.

## Providers

`apps/<app>/search_providers.py` is imported by `CoreConfig.ready()` (`autodiscover_modules`). Subclass `QuerySetProvider` (one queryset) or `SearchProvider` (anything else) and call `register(Provider())`.

| Kind | App | Visible to |
| --- | --- | --- |
| `brand`, `model`, `variant`, `chipset`, `tool`, `firmware` | firmwares | Public, active, staff-approved rows only (unapproved firmware is never returned). |
| `blog`, `blog_category` | blog | Live posts; hidden when the blog is disabled for the visitor. |
| `tag` | tags | Public tags that label a live post. |
| `page` | pages | Published pages. |
| `country`, `calling_code` | core | Pick-only (`navigable = False`): never in header/results search, only when named in `kinds`. Static dataset in `apps/core/geo`. |
| `user` | users | `staff_only`: invisible to everyone else, including through `kinds=user`. |

Provider attributes: `kind`, `label`, `label_plural`, `icon`, `priority` (lower first), `staff_only`, `navigable`, `min_length`, `supports_browse`. Registering a different class for an existing kind raises `ValueError`; the same class is idempotent.

## Behaviour

- `search(query, *, kinds=None, limit=10, user=None, filters=None) -> list[SearchHit]`; `search_grouped(...)` for the results page.
- Queries are trimmed, capped at 100 characters and tokenised (accent-folded, at most 6 words). Every word must match some field.
- Ranking: exact > prefix > word prefix > substring, with a bonus for earlier fields (a name beats a description); ties by provider priority, then title. Deterministic.
- A query shorter than a provider's `min_length` skips that provider. An empty query returns a browse list only when `kinds` names providers with `supports_browse` (pickers), never for header search.
- A provider that raises `DatabaseError` is logged and skipped; the rest still answer.
- `SearchHit.url` is a same-site path (empty for pick-only kinds). Templates render links only for paths starting with a single `/`.

## Pickers

`mode=pick` options carry `data-value`, `data-label`, `data-kind` and provider `extra` pairs as `data-*` attributes (countries expose `data-calling-code`). Cascades send `filter.<param>` from the parent combobox. See "Combobox contract" in `docs/05-frontend/components.md`.

## Tests

`tests/backend/core/test_search_engine.py` (registry, providers, permissions, ranking, XSS, endpoints, request CTAs) and `tests/frontend/test_search_browser.py` (keystroke suggestions, keyboard navigation, hostile queries, responsive overflow).
