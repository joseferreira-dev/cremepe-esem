"""Baixa do certificado: pelo aluno (self-service) e pelo painel administrativo."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.text import slugify

from apps.courses.models import Enrollment

from .services import CertificadoIndisponivel, caminho_pdf, emitir_certificado


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
