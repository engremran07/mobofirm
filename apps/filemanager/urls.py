from django.urls import path

from . import views

app_name = "filemanager"

urlpatterns = [
    path("", views.browser, name="browser"),
    path("upload/", views.upload, name="upload"),
    path("folders/create/", views.create_folder, name="create_folder"),
    path("folders/<int:pk>/rename/", views.rename_folder, name="rename_folder"),
    path("folders/<int:pk>/delete/", views.delete_folder, name="delete_folder"),
    path("trash/", views.trash, name="trash"),
    path("trash/empty/", views.empty_trash, name="empty_trash"),
    path("shares/", views.shares, name="shares"),
    path("shares/<int:pk>/revoke/", views.revoke_share, name="revoke_share"),
    path("s/<str:token>/", views.shared_file, name="shared_file"),
    path("<int:pk>/", views.file_detail, name="file_detail"),
    path("<int:pk>/download/", views.download, name="download"),
    path("<int:pk>/thumbnail/", views.thumbnail, name="thumbnail"),
    path("<int:pk>/rename/", views.rename_file, name="rename_file"),
    path("<int:pk>/move/", views.move_file, name="move_file"),
    path("<int:pk>/tags/", views.set_tags, name="set_tags"),
    path("<int:pk>/delete/", views.delete_file, name="delete_file"),
    path("<int:pk>/restore/", views.restore_file, name="restore_file"),
    path("<int:pk>/purge/", views.purge_file, name="purge_file"),
    path("<int:pk>/share/", views.create_share, name="create_share"),
    path("<int:pk>/versions/upload/", views.upload_version, name="upload_version"),
    path(
        "<int:pk>/versions/<int:version_pk>/restore/",
        views.restore_version,
        name="restore_version",
    ),
    path(
        "<int:pk>/versions/<int:version_pk>/delete/",
        views.delete_version,
        name="delete_version",
    ),
    path(
        "<int:pk>/versions/<int:version_pk>/download/",
        views.download_version,
        name="download_version",
    ),
]
