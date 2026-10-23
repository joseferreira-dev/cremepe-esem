"""Views do aluno (área de cursos)."""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.accounts.decorators import login_required_limpo
from apps.accounts.tokens import gerar_token_api

from .models import Course, Enrollment, Progress
from .services import course_coverage, format_hours, unlocked_videos


@never_cache
def inicio(request):
    """Pagina publica de apresentacao da ESEM (ponto de entrada do portal)."""
    return render(request, "inicio.html", {"usuario_autenticado": request.user.is_authenticated})


@never_cache
@login_required_limpo
def my_courses(request):
    if request.user.is_staff:
        cursos = []
        for curso in Course.objects.prefetch_related("videos", "enrollments"):
            matriculas = list(curso.enrollments.all())
            total = len(matriculas)
            media = (
                sum(course_coverage(matricula) for matricula in matriculas) / total
                if total
                else 0
            )
            cursos.append(
                {
                    "curso": curso,
                    "videos": list(curso.videos.all()),
                    "duration": format_hours(curso.total_duration),
                    "total": total,
                    "aprovados": sum(
                        1
                        for matricula in matriculas
                        if matricula.status == Enrollment.Status.APPROVED
                    ),
                    "media": round(media, 1),
                }
            )
        return render(
            request,
            "courses/my_courses.html",
            {"modo_admin": True, "cursos_admin": cursos},
        )

    enrollments = (
        Enrollment.objects.filter(medico__user=request.user)
        .select_related("course")
        .prefetch_related("course__videos")
        .order_by("-created_at")
    )

    items = []
    for enrollment in enrollments:
        curso = enrollment.course
        # Rascunhos nunca aparecem para o aluno, mesmo com matrícula.
        if curso.status == Course.Status.DRAFT:
            continue
        videos = [v for v in curso.videos.all() if v.disponivel]
        bloqueado = (
            curso.status == Course.Status.ARCHIVED
            or curso.expirado
            or bool(enrollment.deadline and enrollment.deadline < timezone.localdate())
        )
        items.append(
            {
                "enrollment": enrollment,
                "coverage": course_coverage(enrollment),
                "videos": videos,
                "duration": format_hours(curso.total_duration),
                "bloqueado": bloqueado,
                "motivo": (
                    "Arquivado"
                    if curso.status == Course.Status.ARCHIVED
                    else (
                        "Prazo encerrado"
                        if curso.expirado
                        or (enrollment.deadline and enrollment.deadline < timezone.localdate())
                        else ""
                    )
                ),
            }
        )

    return render(request, "courses/my_courses.html", {"items": items})


@never_cache
@login_required_limpo
def course_view(request, enrollment_id: int):
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("course", "medico"), pk=enrollment_id
    )
    if enrollment.medico.user_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied

    # Rascunho, arquivado ou fora do prazo: o aluno não acessa a página do curso.
    if not request.user.is_staff:
        prazo_matricula = enrollment.deadline and enrollment.deadline < timezone.localdate()
        if enrollment.course.indisponivel_para_aluno or prazo_matricula:
            raise PermissionDenied

    videos = [v for v in enrollment.course.videos.all() if v.disponivel]
    unlocked = {video.id for video in unlocked_videos(enrollment)}
    progresses = {
        progress.video_id: progress
        for progress in Progress.objects.filter(enrollment=enrollment)
    }

    items = []
    for video in videos:
        progress = progresses.get(video.id)
        items.append(
            {
                "video": video,
                "progress": progress,
                "unlocked": video.id in unlocked,
                "covered": progress.covered if progress else 0.0,
                "percent": round(
                    (progress.covered / video.duration * 100)
                    if progress and video.duration
                    else 0.0,
                    1,
                ),
                "last_position": progress.last_position if progress else 0.0,
            }
        )

    return render(
        request,
        "courses/course.html",
        {
            "enrollment": enrollment,
            "items": items,
            "coverage": course_coverage(enrollment),
            "required": float(enrollment.min_percent or 0),
            "duration": format_hours(enrollment.course.total_duration),
            # Token de API: permite ao player funcionar mesmo em ambientes que
            # reescrevem o cabecalho Cookie das requisicoes fetch.
            "api_token": gerar_token_api(
                request.user,
                request.session.session_key,
                validade=settings.API_TOKEN_TTL_SECONDS,
            ),
        },
    )


@never_cache
@require_GET
@login_required_limpo
def player_token(request, enrollment_id: int):
    """Entrega um token novo ao player usando a sessao da propria pagina.

    A rota fica sob ``/cursos/`` para receber tambem cookies de sessao com
    Path=/cursos (cookie antigo/duplicado que a API em /api nao recebe).
    """
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("medico"), pk=enrollment_id
    )
    if enrollment.medico.user_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied
    return JsonResponse(
        {
            "api_token": gerar_token_api(
                request.user,
                request.session.session_key,
                validade=settings.API_TOKEN_TTL_SECONDS,
            )
        },
        headers={"Cache-Control": "no-store"},
    )
