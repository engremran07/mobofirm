from django.urls import path

from . import views

app_name = "gamification"

urlpatterns = [
    path("community/badges/", views.badges, name="badges"),
    path("community/progress/", views.progress, name="progress"),
    path("leaderboard/", views.leaderboard, name="leaderboard"),
]
