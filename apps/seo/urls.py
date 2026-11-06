from django.urls import path

from . import views

app_name = "seo"

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("inventory/", views.inventory, name="inventory"),
    path("inspect/", views.inspect, name="inspect"),
    path("issues/", views.issues, name="issues"),
    path("ledger/", views.ledger_view, name="ledger"),
    path("ledger/<int:pk>/<slug:action>/", views.ledger_action, name="ledger_action"),
    path("jobs/", views.jobs_view, name="jobs"),
    path("jobs/process/", views.process_jobs, name="process_jobs"),
    path("crawl/", views.start_crawl, name="start_crawl"),
    path("settings/", views.settings_view, name="settings"),
]
