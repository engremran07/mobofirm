from django.urls import path

from . import views

app_name = "governance"

urlpatterns = [
    path("cases/", views.case_list, name="case-list"),
    path("cases/<int:case_id>/review/", views.review_case, name="case-review"),
    path("cases/<int:case_id>/decision/", views.decide_case_view, name="case-decision"),
    path("cases/<int:case_id>/fulfill/", views.fulfill_case_view, name="case-fulfill"),
    path("cases/<int:case_id>/report/", views.report_issue_view, name="case-report"),
]
