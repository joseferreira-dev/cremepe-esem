from django.urls import path

from . import views

urlpatterns = [
    # Upload em blocos (admin)
    path("uploads/pagina/", views.upload_page, name="upload_page"),
    path("uploads/init/", views.upload_init, name="upload_init"),
    path(
        "uploads/<uuid:upload_id>/chunk/",
        views.upload_chunk,
        name="upload_chunk",
    ),
    path(
        "uploads/<uuid:upload_id>/complete/",
        views.upload_complete,
        name="upload_complete",
    ),
    path(
        "uploads/<uuid:upload_id>/status/",
        views.upload_status,
        name="upload_status",
    ),
    # Player
    path("player/state/", views.player_state, name="player_state"),
    path("player/heartbeat/", views.heartbeat, name="heartbeat"),
    # Mídia
    path("media/<int:video_id>/master.m3u8", views.master_manifest, name="master_manifest"),
    path(
        "media/<int:video_id>/<str:quality>/index.m3u8",
        views.variant_manifest,
        name="variant_manifest",
    ),
    path(
        "media/<int:video_id>/<str:quality>/<str:segment_name>",
        views.segment,
        name="segment",
    ),
    path("media/<int:video_id>/poster.jpg", views.poster, name="poster"),
]
