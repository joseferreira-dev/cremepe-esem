"""Rotas do projeto."""

from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path

from apps.accounts import views as accounts_views
from apps.courses import views as courses_views


def healthz(request):
    """Verificação de saúde usada pelo monitoramento do servidor."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001
        return JsonResponse({"status": "erro", "banco": False}, status=503)
    return JsonResponse({"status": "ok", "banco": True})


urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("entrar/", accounts_views.login_view, name="login"),
    path("sair/", accounts_views.logout_view, name="logout"),
    path("trocar-senha/", accounts_views.password_change_view, name="password_change"),
    path("", courses_views.inicio, name="inicio"),
    path("sobre/", courses_views.sobre, name="sobre"),
    path("cursos/", courses_views.my_courses, name="my_courses"),
    path(
        "cursos/<int:enrollment_id>/token-player/",
        courses_views.player_token,
        name="player_token",
    ),
    path("cursos/<int:enrollment_id>/", courses_views.course_view, name="course"),
    path("painel/", include("apps.panel.urls")),
    path("api/", include("apps.media.urls")),
]
