"""Baixa do certificado: pelo aluno (self-service) e pelo painel administrativo."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.courses.models import Enrollment

from .models import Certificate
from .services import CertificadoIndisponivel, caminho_pdf, emitir_certificado


@never_cache
@login_required
@require_GET
def meus_certificados(request):
    """Página do aluno: certificados obtidos e matrículas já aptas à emissão.

    A lista mostra só o que é do próprio usuário (staff vê apenas o seu, já
    que o painel cobre o acesso aos demais). A primeira baixa emite o PDF —
    aqui nada é emitido, apenas sinalizado como pronto.
    """
    emitidos = (
        Certificate.objects.filter(enrollment__medico__user=request.user)
        .select_related("enrollment", "enrollment__course")
        .order_by("-issued_at")
    )
    prontos = (
        Enrollment.objects.filter(
            medico__user=request.user,
            status=Enrollment.Status.APPROVED,
            certificate__isnull=True,
        )
        .select_related("course")
        .order_by("-approved_at")
    )
    return render(
        request,
        "certificates/meus_certificados.html",
        {"emitidos": emitidos, "prontos": prontos},
    )


def _servir(request, enrollment: Enrollment, voltar_para: str) -> HttpResponse:
    """Emite (se preciso) e devolve o PDF como anexo.

    O PDF (~250 KB) vai em memória: dispensa streaming e evita que o
    ``close()`` de uma resposta streamable dispare ``request_finished``
    no meio de uma transação.
    """
    try:
        certificate = emitir_certificado(
            enrollment, actor=request.user, ip=request.META.get("REMOTE_ADDR")
        )
    except CertificadoIndisponivel as erro:
        messages.error(request, str(erro))
        return redirect(voltar_para)

    nome = f"certificado-{slugify(enrollment.medico.nome) or 'aluno'}.pdf"
    resposta = HttpResponse(
        caminho_pdf(certificate.code).read_bytes(), content_type="application/pdf"
    )
    resposta["Content-Disposition"] = f'attachment; filename="{nome}"'
    return resposta


@login_required
def certificado_aluno(request, enrollment_id: int):
    """O aluno só baixa o próprio certificado (staff pode conferir o de qualquer um)."""
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("course", "medico"), pk=enrollment_id
    )
    if enrollment.medico.user_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied
    return _servir(request, enrollment, reverse("course", args=[enrollment.pk]))


def certificado_painel(request, pk: int):
    """Painel: o acesso é garantido pelo decorator ``somente_admin`` da URL."""
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("course", "medico"), pk=pk
    )
    return _servir(request, enrollment, reverse("painel_matriculas"))
