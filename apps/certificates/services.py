"""Emissão do certificado em PDF — modelo oficial da ESEM (A4 paisagem).

O modelo ``Modelo de Certificado em Branco I.docx`` traz fundo com moldura e
logos, texto centralizado (abertura/nome 12 e 20 pt) e duas assinaturas:
CREMEPE à esquerda (Mário Jorge Lemos de Castro Lôbo) e ESEM à direita
(Virgílio Silveira Carneiro Leão Filho). Os assets foram extraídos desse
modelo e são reaproveitados aqui, com o texto dinamizado para o curso real.
"""

from __future__ import annotations

import base64
import secrets
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from apps.courses.models import Enrollment
from apps.courses.services import course_coverage, course_videos, record_audit

from .models import Certificate

MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)

# Chaves de imagem: entram no HTML como data URI, mas não no payload auditado.
_CHAVES_DE_IMAGEM = ("fundo", "assinatura_cremepe", "assinatura_essem")


class CertificadoIndisponivel(Exception):
    """A matrícula não está aprovada — o certificado ainda não pode sair."""


def _data_uri(nome: str) -> str:
    """Asset embutido no HTML (evita depender de URL/estáticos no render)."""
    conteudo = (Path(__file__).resolve().parent / "assets" / nome).read_bytes()
    extensao = nome.rsplit(".", 1)[-1]
    return "data:image/%s;base64,%s" % (
        extensao,
        base64.b64encode(conteudo).decode("ascii"),
    )


def caminho_pdf(codigo: str) -> Path:
    return Path(settings.MEDIA_ROOT) / "certificados" / f"{codigo}.pdf"


def gerar_codigo() -> str:
    """Código de autenticação curto e verificável: ESEM-2026-A1B2C3D4."""
    return f"ESEM-{timezone.localdate().year}-{secrets.token_hex(4).upper()}"


def carga_horaria(segundos: float) -> str:
    total = int(segundos or 0)
    horas, resto = divmod(total, 3600)
    minutos, _ = divmod(resto, 60)
    if horas:
        partes = [f"{horas} hora{'s' if horas > 1 else ''}"]
        if minutos:
            partes.append(f"{minutos} minuto{'s' if minutos > 1 else ''}")
        return " e ".join(partes)
    if minutos:
        return f"{minutos} minuto{'s' if minutos > 1 else ''}"
    return f"{total} segundo{'s' if total != 1 else ''}"


def dados_do_certificado(enrollment: Enrollment, codigo: str) -> dict:
    """Valores impressos no PDF (e congelados depois no payload)."""
    curso = enrollment.course
    segundos = sum(video.duration or 0 for video in course_videos(curso))
    hoje = timezone.localdate()
    return {
        "aluno": enrollment.medico.nome,
        "curso": curso.title,
        "edicao": curso.edition,
        "carga": carga_horaria(segundos),
        "data": f"{hoje.day} de {MESES[hoje.month - 1]} de {hoje.year}",
        "codigo": codigo,
        "percentual": round(course_coverage(enrollment), 1),
        "fundo": _data_uri("fundo.png"),
        "assinatura_cremepe": _data_uri("assinatura-cremepe.png"),
        "assinatura_essem": _data_uri("assinatura-essem.png"),
    }


def gerar_pdf(certificate: Certificate) -> Path:
    """Renderiza o PDF do certificado e arquiva os dados junto dele."""
    enrollment = (
        Enrollment.objects.select_related("course", "medico")
        .get(pk=certificate.enrollment_id)
    )
    dados = dados_do_certificado(enrollment, certificate.code)
    html = render_to_string("certificates/certificado.html", dados)

    destino = caminho_pdf(certificate.code)
    destino.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html).write_pdf(str(destino))

    certificate.pdf_path = str(destino.relative_to(settings.MEDIA_ROOT))
    certificate.payload = {k: v for k, v in dados.items() if k not in _CHAVES_DE_IMAGEM}
    certificate.save(update_fields=["pdf_path", "payload"])
    return destino


def emitir_certificado(
    enrollment: Enrollment, actor=None, ip: str | None = None
) -> Certificate:
    """Emite (ou recupera) o certificado da matrícula.

    Só matrículas **aprovadas** podem ser emitidas. A primeira emissão é
    registrada na auditoria; baixas seguintes reutilizam o mesmo arquivo.
    """
    if enrollment.status != Enrollment.Status.APPROVED:
        raise CertificadoIndisponivel(
            "O certificado só é emitido após a aprovação da matrícula."
        )

    certificate = Certificate.objects.filter(enrollment=enrollment).first()
    if certificate is None:
        for tentativa in range(5):
            try:
                with transaction.atomic():
                    certificate = Certificate.objects.create(
                        enrollment=enrollment,
                        issued_by=actor if getattr(actor, "is_staff", False) else None,
                        code=gerar_codigo(),
                    )
                break
            except IntegrityError:  # colisão rara de código: sorteia outro
                continue
        else:
            raise CertificadoIndisponivel(
                "Não foi possível gerar o código de autenticação. Tente novamente."
            )
        record_audit(
            "certificado_emitido",
            actor=actor,
            target=certificate.code,
            detail={
                "matricula": enrollment.pk,
                "aluno": enrollment.medico.nome,
                "curso": enrollment.course.title,
            },
            ip=ip,
        )

    if not caminho_pdf(certificate.code).exists():
        gerar_pdf(certificate)
    return certificate
