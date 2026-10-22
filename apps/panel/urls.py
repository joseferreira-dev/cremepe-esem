from django.urls import path

from . import views
from .decorators import somente_admin

urlpatterns = [
    path("", somente_admin(views.index), name="painel_index"),
    # Cursos
    path("cursos/", somente_admin(views.cursos), name="painel_cursos"),
    path("cursos/novo/", somente_admin(views.curso_form), name="painel_curso_novo"),
    path("cursos/<int:pk>/", somente_admin(views.curso_detalhe), name="painel_curso"),
    path(
        "cursos/<int:pk>/editar/",
        somente_admin(views.curso_form),
        name="painel_curso_editar",
    ),
    path(
        "cursos/<int:pk>/excluir/",
        somente_admin(views.curso_remover),
        name="painel_curso_remover",
    ),
    path(
        "cursos/<int:pk>/exportar/",
        somente_admin(views.matricula_exportar),
        name="painel_curso_exportar",
    ),
    # Vídeos
    path("videos/novo/", somente_admin(views.video_form), name="painel_video_novo"),
    path("videos/<int:pk>/", somente_admin(views.video_detalhe), name="painel_video"),
    path(
        "videos/<int:pk>/editar/",
        somente_admin(views.video_form),
        name="painel_video_editar",
    ),
    path(
        "videos/<int:pk>/reprocessar/",
        somente_admin(views.video_reprocessar),
        name="painel_video_reprocessar",
    ),
    path(
        "videos/<int:pk>/excluir/",
        somente_admin(views.video_remover),
        name="painel_video_remover",
    ),
    # Médicos
    path("medicos/", somente_admin(views.medicos), name="painel_medicos"),
    path("medicos/novo/", somente_admin(views.medico_form), name="painel_medico_novo"),
    path(
        "medicos/<int:pk>/editar/",
        somente_admin(views.medico_form),
        name="painel_medico_editar",
    ),
    path(
        "medicos/<int:pk>/acao/",
        somente_admin(views.medico_acao),
        name="painel_medico_acao",
    ),
    # Matrículas
    path("matriculas/", somente_admin(views.matriculas), name="painel_matriculas"),
    path(
        "matriculas/nova/",
        somente_admin(views.matricula_form),
        name="painel_matricula_nova",
    ),
    path(
        "matriculas/<int:pk>/acao/",
        somente_admin(views.matricula_acao),
        name="painel_matricula_acao",
    ),
    # Auditoria
    path("auditoria/", somente_admin(views.auditoria), name="painel_auditoria"),
]
