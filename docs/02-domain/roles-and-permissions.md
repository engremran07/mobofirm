# Roles and Permissions

## Authorization authority

Django authentication and each endpoint/service permission check are authoritative. A role label or frontend affordance is not permission by itself.

## User role values

`users.CustomUser.Roles` currently defines:

| Value | Meaning in model | Important limitation |
| --- | --- | --- |
| `admin` | Administrative role value | `is_admin()` includes superuser or the admin role; endpoint permissions still require review. |
| `editor` | Editorial role value | No universal capability matrix is implied by the role name. |
| `author` | Author role value | Content ownership and the specific endpoint checks remain relevant. |
| `moderator` | Moderation role value | Do not assume it grants any Django model permission; moderation actions check the relevant `change_*` permission. |
| `reader` | Reader role value | Baseline role; authenticated/public access is endpoint-specific. |

`is_staff`, `is_superuser`, Django model permissions, and application roles are distinct mechanisms. Avoid granting access based only on a guessed role hierarchy.

## Moderation permissions

- Moderation is app-owned; there is no separate governance app or four-eyes rule.
- `firmwares.change_pendingfirmware` authorizes approving, rejecting and withdrawing firmware uploads.
- `blog.change_post` (staff) authorizes publishing, scheduling and archiving posts; the post author or staff may request review.
- `comments.change_comment` (staff) authorizes comment approval, rejection and spam decisions.
- `tags.change_tag` (staff, via the admin) authorizes approving or rejecting suggested tags.
- Each decision records who decided, when and why on the owning record (or in the structured event log for posts).

## Security requirements

- Every new API/view must declare its authentication and authorization behavior.
- Enforce object-level ownership in server-side queries/actions.
- Test unauthorized, unauthenticated, cross-owner, and privileged paths as applicable.
- Product and security owners must approve any role or permission-policy change.
