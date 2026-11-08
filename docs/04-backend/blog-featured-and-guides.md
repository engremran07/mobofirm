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

`blog.GuideRequest` (requester, title, description, kind = flashing method | unlock
pattern | trick | guide, optional brand/model/chipset). State machine, all atomic,
row-locked and idempotent (`apps.blog.guide_requests`):

```
pending -> approved | rejected (reason) | cancelled
approved -> fulfilled | cancelled        rejected / fulfilled / cancelled are final
```

* Create: login + verified email, `BlogSettings.allow_guide_requests`, throttled by
  `guide_requests_per_day` and `guide_requests_open_limit`, duplicate detection against
  live posts, public requests and other members' pending requests.
* **Private until staff approve** (review-only Unfold queue *Content > Guide requests*,
  permission `blog.change_guiderequest`); the requester is notified of every decision.
* Approved requests are public (`/blog/requests/`) and the only ones that can carry a
  bounty (`apps.bounty` kind `guide`; blog reaches bounty only through
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
