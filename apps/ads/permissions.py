def can_manage_ads(user: object) -> bool:
    return bool(
        getattr(user, "is_staff", False)
        or getattr(user, "is_superuser", False)
        or getattr(user, "has_role", lambda *roles: False)("admin", "editor")
    )
