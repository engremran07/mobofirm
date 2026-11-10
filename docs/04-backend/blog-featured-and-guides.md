# Featured articles and guide requests (`apps.blog`, `apps.bounty`)

## Post access

`blog.Post.access` is `free`, `featured` or `paid`. `price` is the **staff-approved USD
price** (credits are derived only through `apps.wallet.api.to_credits`, the single
rate); `proposed_price` is the author's suggestion. Publishing or scheduling a gated post
as staff approves the proposed price (`price_approved_by/at`); a paid post cannot go live
without one, and a price set by staff in the admin is never overwritten. Setting a post
back to `free` clears every price field.

Who may *choose* featured or paid when writing (`apps.blog.access.can_set_premium_access`):
staff, editors, users with the author role and approved marketplace sellers (reached only
through `AppService.get("marketplace").is_active_seller`). Everyone else submits free.

## Who reads the full body

`apps.blog.paywall.resolve_gate(user, post)`:

1. free post: everyone;
2. staff, editors and the author: always;
3. everyone else: the shop decides through `apps.blog.shop_gateway`
   (`AppService.get("shop")`): `featured_blog_access(user, post_id, price_usd)` is allowed
   for lifetime buyers and holders of a package with the `featured_blog` item type (the
   package consumes its quota; they are never charged again);
4. otherwise (denied, shop absent, switched off or contract missing) the **teaser**.

The teaser is the first `BlogSettings.teaser_words` words (never more than 40 % of a short
article), the paywall box (`.article-paywall`) and a *Buy with credits* dialog
(`blog:purchase_dialog`, idempotent key `blog-featured:<user>:<post>`; the shop spends
through the derived wallet sink `blog.featured_purchase` and refunds on failure) or a
*See packages* link. Gated pages are `Cache-Control: private`.

No leakage: cards, RSS/Atom/JSON feeds, global search snippets, meta/OG descriptions and
structured data use `paywall.public_excerpt` / `teaser_text` only; blog search does not
match a gated body; the sitemap lists URLs only. `BlogPosting` carries
`isAccessibleForFree: false` and a `hasPart` with `cssSelector: .article-paywall`.

## Guide requests

`blog.GuideRequest` (requester, title, kind = flashing method | unlock pattern | trick |
guide, optional brand/model/variant/chipset, typed `model_code`/`variant_code`,
**`tried`**, expected outcome, optional bounty offer, `related_post`, search keywords).
State machine, all atomic, row-locked and idempotent (`apps.blog.guide_requests`):

```
pending -> approved | rejected (reason) | cancelled
approved -> fulfilled | cancelled        rejected / fulfilled / cancelled are final
```

### Search first, then ask (two steps)

1. **Search** (`/blog/requests/new/`, `apps.blog.guide_requests_search`): the member enters
   the **model code and/or variant code** (required) plus optional brand, chipset and topic
   keywords. HTMX (`/blog/requests/new/search/`, debounced) lists *all* related published
   articles: same variant code, same model, same chipset, same brand, matching tag or fuzzy
   text (typos tolerated; matching goes through `apps.core.search.listing.search_queryset`,
   the shared engine). Each row shows title, device, why it matched, an access badge
   (Free / Featured / Paid) and **This solved it**. Results carry **no body and no snippet**
   and the body is never searched, so gated text cannot leak.
2. Only after the list is shown does the member get *None of these solve it - request a new
   method*: a link with a signed, user-bound, 6 hour token (`ctx`). `/blog/requests/new/form/`
   and its POST require that token, so the search cannot be skipped.

**Article button.** Every article page renders *Didn't resolve your issue? Request a new
method* (`{% request_method_cta post %}`, hidden while requests are off). It goes through
`/blog/requests/from/<slug>/`, which mints a token for the article and opens the form
pre-filled with the article (`related_post`), its model/brand/chipset and code, focused on
**What have you tried?**.

**Request form** (every field has an example placeholder and help text): title, kind,
brand/model/chipset pickers, model code, variant code (a model **or** variant code is
required; typed codes link to the catalogue when they match exactly), **What have you tried?**
(required, at least 20 characters: tools, steps, error messages, firmware version), expected
outcome, optional bounty offer in credits (an intent; the bounty is still escrowed from the
request page after approval), optional extra details.

**Duplicates.** A live post or public request with a near-identical title blocks the request
(the article the member says did not help is excluded). Open public requests for the same
model/variant and the same topic (same kind or alike title) are listed with *Follow this
request*; the member can follow instead (`GuideRequestFollower`, "me too", milestone notices to
the requester, followers notified on fulfilment) or confirm *My request is different*.
Pending requests of other members stay private and block only a same-device lookalike.

**Who sees the context.** `tried`, the expected outcome and the related article are shown to
the requester, staff reviewers (request page and the Unfold review queue) and, once the
request is approved, to verified members acting as bounty hunters, so they can write their own
solution. Anonymous visitors only see the public summary.

* Create: login + verified email, `BlogSettings.allow_guide_requests`, throttled by
  `guide_requests_per_day` and `guide_requests_open_limit`, duplicate detection as above.
* **Private until staff approve** (review-only Unfold queue *Content > Guide requests*,
  permission `blog.change_guiderequest`, showing the tried text, device codes, related
  article and followers); the requester is notified of every decision.
* Approved requests are public (`/blog/requests/`, typo-tolerant search) and the only ones
  that can carry a bounty (`apps.bounty` kind `guide`; blog reaches bounty only through
  `AppService.get("bounty")`).
* Hunters submit through `/blog/create/?solves=<id>`: they **must** choose free, featured
  or paid (paid needs a proposed price); non-trusted members can only choose free. The
  submission goes straight to *In review* and is shown on the request page with its access.
* Staff publish the submission: after commit `blog.signals._after_publish` fulfils the
  request, publishes `blog.guide_fulfilled` (bounty releases the escrow to the author through
  the wallet and settles every open bounty on it), `blog.post_published` (gamification) and
  grants the wallet rewards. Cancelling or rejecting publishes `blog.guide_request_closed`
  and bounty refunds the escrows. `blog.tasks.reconcile_guide_fulfilments` re-runs a
  fulfilment that crashed between publishing and settling (idempotent).

## Rewards and spend

| Key | Kind | Default | Trigger |
|-----|------|---------|---------|
| `blog.post_published` | reward | 10 | member post published by another staff member |
| `blog.post_published.featured` | reward | 25 | extra for a featured or paid post |
| `blog.guide_published` | reward | 15 | replaces the base reward for a guide solution |
| `bounty.fulfilled` | reward | escrow | settlement of the bounty (amount is the escrow) |
| `blog.featured_purchase` | spend (derived) | post price x rate | the shop's `purchase_featured_blog` |

Rewards are admin-editable in *Credits economy > Reward rules*. Own posts, automated
posts and posts a member published themselves earn nothing.

## Optional shop extras

`featured_blog_buyers(post_id)` fills the read-only *Buyers* field of the Post admin and
`purchased_featured_blog_ids(user)` feeds `/blog/purchased/`. Without them both show an
honest empty state; access itself needs only `featured_blog_access` and
`purchase_featured_blog`.
