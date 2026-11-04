# Django App Boundaries

Each app owns its models and domain rules. The goal is that each domain app can be installed and reused without requiring unrelated sibling apps. Cross-app integrations use explicit optional service/API contracts or narrowly scoped signals; do not duplicate authoritative state.

| App | Ownership | Principal integration boundary |
|---|---|---|
| `users` | `CustomUser`, authentication integration, profiles/following/activity, MFA and notifications | Other apps reference `AUTH_USER_MODEL`; device and content workflows may request notifications through user services. |
| `core` | Shared settings/helpers, health endpoints, logging support, feature flags, cleanup command | Shared infrastructure only; cleanup imports the locally owned firmware/ad models for specifically bounded retention. |
| `ads` | Ad networks, units, campaigns, creatives, placements, events, affiliate data and settings | Consumers call ad services/routes; consent and provider credentials remain server-controlled. |
| `devices` | Device identity, policy/configuration, device events, fingerprints and behavior insights | Login/device policy is enforced through device services; user ownership is FK-based. |
| `governance` | Review cases, append-only review events, decisions and fulfillment orchestration | Supported models are explicitly allowlisted: blog post, comment, pending firmware, and tag. |
| `blog` | Posts, categories, revisions/translations, feeds and editorial behavior | Governance controls covered publication; `PostRevision` is the single content-history source; uncurated automated tag suggestions are not attached to published content. |
| `comments` | Comment state, moderation and comment-specific interactions | Comments reference blog posts and can enter governance review. |
| `firmwares` | Device/brand catalog, uploads, firmware records, download tokens/sessions, gates, changelogs and guides | Pending uploads enter governance; generated blog guides use the blog history/review service and never publish directly; only active, routed or explicitly verified records are exposed in live blog inventory; download flow is locally owned. |
| `tags` | Tag records, generic tagged-item relations, discovery and curation | Blog/comment associations and governance review for AI-suggested tags. |

## Standalone-app status and rules

- Add a model to the app that owns its lifecycle, not to `core` for convenience.
- Keep API/UI authorization inside the owning app.
- Before adding a cross-app import, verify the target app is installed and the public contract exists.
- Test cross-app event effects, duplicate delivery, and failure semantics.
- Keep migration dependencies acyclic where possible and use app labels from `apps.py`.
- Cross-app imports must target only installed apps and documented contracts; do not restore obsolete integrations.
- Installed apps are connected in this project, but standalone installation has not yet been verified for all apps. Existing sibling imports and migration/model dependencies must be inventoried and removed or made optional before claiming the portability goal is complete.
- Keep project-level tests in `tests/backend/` and `tests/frontend/`; integration tests spanning app ownership belong in `tests/backend/integrations/`.
- New direct sibling imports are prohibited unless documented as an explicit integration boundary with tests. Prefer app-owned optional adapters or caller-provided contracts over mandatory sibling imports.
