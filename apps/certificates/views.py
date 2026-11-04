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
from apps.courses.services import record_audit

from .models import Certificate
from .services import CertificadoIndisponivel, caminho_pdf, emitir_certificado

# Limite de consultas com código errado por sessão: o código tem 8 bytes
# hexadecimais e o limite torna a enumeração brute-force impraticável.
TENTATIVAS_MAX_POR_SESSAO = 10


@never_cache
@login_required
@require_GET
def meus_certificados(request):
    """Página do aluno: certificados obtidos e matrículas já aptas à emissão.

    Área do aluno — administradores não entram: a emissão de terceiros é do
    painel (``painel_certificado``). Lista só o que é do próprio usuário.
    A primeira baixa emite o PDF; aqui nada é emitido, apenas sinalizado.
    """
    if request.user.is_staff:
        raise PermissionDenied(
            "Área do aluno: administradores emitem pelo painel."
        )
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


@never_cache
@require_GET
def validar_certificado(request):
    """Validação pública de certificados — qualquer pessoa, sem login.

    Consulta pelo código de autenticação (``?codigo=ESEM-2026-XXXXXXXXXXXX``).
    Só expõe o que já está impresso no PDF (nome, curso, edição, emissão e
    carga horária): nunca CPF ou outros dados cadastrais. Cada acerto é
    registrado na auditoria.
    """
    codigo = (request.GET.get("codigo") or "").strip().upper()
    certificate = None
    bloqueado = False

    if codigo:
        tentativas = request.session.get("validacao_tentativas", 0)
        if tentativas >= TENTATIVAS_MAX_POR_SESSAO:
            bloqueado = True
        else:
            certificate = (
                Certificate.objects.select_related(
                    "enrollment__course", "enrollment__medico"
                )
                .filter(code__iexact=codigo)
                .first()
            )
            if certificate is None:
                request.session["validacao_tentativas"] = tentativas + 1
            else:
                request.session["validacao_tentativas"] = 0
                record_audit(
                    "certificado_validado",
                    target=certificate.code,
                    detail={
                        "aluno": certificate.enrollment.medico.nome,
                        "curso": certificate.enrollment.course.title,
                    },
                    ip=request.META.get("REMOTE_ADDR"),
                )

    return render(
        request,
        "certificates/validar.html",
        {
            "codigo": codigo,
            "certificate": certificate,
            "bloqueado": bloqueado,
            "link": request.build_absolute_uri(f"/validar/?codigo={codigo}")
            if certificate
            else "",
        },
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
    """O aluno só baixa o próprio certificado.

    Administradores emitem pelo painel (``painel_certificado``); a rota do
    aluno fica reservada ao próprio titular.
    """
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("course", "medico"), pk=enrollment_id
    )
    if enrollment.medico.user_id != request.user.id:
        raise PermissionDenied
    return _servir(request, enrollment, reverse("course", args=[enrollment.pk]))


def certificado_painel(request, pk: int):
    """Painel: o acesso é garantido pelo decorator ``somente_admin`` da URL."""
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("course", "medico"), pk=pk
    )
    return _servir(request, enrollment, reverse("painel_matriculas"))
