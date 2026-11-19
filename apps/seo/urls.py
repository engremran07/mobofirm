from django.urls import path

from . import views

app_name = "seo"

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("scan/", views.run_scan, name="run_scan"),
    path("inventory/", views.inventory, name="inventory"),
    path("sitemaps/", views.sitemaps, name="sitemaps"),
    path("inspect/", views.inspect, name="inspect"),
    path("issues/", views.issues, name="issues"),
    path("issues/bulk/", views.issues_bulk, name="issues_bulk"),
    path("issues/<int:pk>/", views.issue_detail, name="issue_detail"),
    path("fix/", views.fix_screen, name="fix"),
    path("fix/apply/", views.fix_apply, name="fix_apply"),
    path("fix/preview/", views.fix_preview, name="fix_preview"),
    path("issues/<int:pk>/apply/", views.issue_apply, name="issue_apply"),
    path("issues/<int:pk>/ignore/", views.issue_ignore, name="issue_ignore"),
    path("issues/<int:pk>/restore/", views.issue_restore, name="issue_restore"),
    path("issues/<int:pk>/reaudit/", views.issue_reaudit, name="issue_reaudit"),
    path("ledger/", views.ledger_view, name="ledger"),
    path("links/", views.links, name="links"),
    path("links/generate/", views.links_generate, name="links_generate"),
    path("links/bulk/", views.links_bulk, name="links_bulk"),
    path("links/<int:pk>/<slug:decision>/", views.link_decide, name="link_decide"),
    path("guides/", views.guides, name="guides"),
    path("guides/generate/", views.guides_generate, name="guides_generate"),
    path("ledger/<int:pk>/<slug:action>/", views.ledger_action, name="ledger_action"),
    path("jobs/", views.jobs_view, name="jobs"),
    path("jobs/process/", views.process_jobs, name="process_jobs"),
    path("crawl/", views.start_crawl, name="start_crawl"),
    path("settings/", views.settings_view, name="settings"),
]
