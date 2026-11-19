from django.contrib.admin.apps import AdminConfig


class MobofirmAdminConfig(AdminConfig):
    """Installs the project admin site (a plain Django ``AdminSite``) as ``admin.site``."""

    default_site = "apps.core.admin_site.MobofirmAdminSite"
