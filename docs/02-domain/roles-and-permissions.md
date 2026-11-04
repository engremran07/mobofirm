# Roles and Permissions

## Authorization authority

Django authentication and each endpoint/service permission check are authoritative. A role label or frontend affordance is not permission by itself.

## User role values

`users.CustomUser.Roles` currently defines:

| Value | Meaning in model | Important limitation |
|---|---|---|
| `admin` | Administrative role value | `is_admin()` includes superuser or the admin role; endpoint permissions still require review. |
| `editor` | Editorial role value | No universal capability matrix is implied by the role name. |
| `author` | Author role value | Content ownership and the specific endpoint checks remain relevant. |
| `moderator` | Moderation role value | Do not assume it grants governance reviewer permission. |
| `reader` | Reader role value | Baseline role; authenticated/public access is endpoint-specific. |

`is_staff`, `is_superuser`, Django model permissions, and application roles are distinct mechanisms. Avoid granting access based only on a guessed role hierarchy.

## Governance permissions

- `governance.can_review_content` authorizes review/decision actions.
- `governance.can_publish_content` authorizes fulfillment/publication actions.
- Covered workflow tests require separate reviewer and publisher permissions for approved content.
- Submission ownership is checked for user-submitted posts, comments, and firmware uploads; staff behavior is explicit in the service.

## Security requirements

- Every new API/view must declare its authentication and authorization behavior.
- Enforce object-level ownership in server-side queries/actions.
- Test unauthorized, unauthenticated, cross-owner, and privileged paths as applicable.
- Product and security owners must approve any role or permission-policy change.
