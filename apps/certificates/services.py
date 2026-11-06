"""Emissão do certificado em PDF — A4 paisagem, com verso de validação.

O anverso usa o **modelo enviado pelo curso** (o .docx do painel, extraído
em ``apps/certificates/docx.py``): o campo dinâmico é só ``NOME COMPLETO``
e a arte do documento vira o fundo. **Todo curso precisa ter modelo para
emissão** — sem ele, a emissão é recusada com mensagem clara.

O **verso** traz os dados de validação e um QR Code que aponta para a
consulta pública ``/validar/?codigo=...``.
"""

from __future__ import annotations

import base64
import io
import logging
import secrets
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from apps.courses.models import Course, Enrollment
from apps.courses.services import course_coverage, course_videos, record_audit

from .docx import ModeloInvalido, extrair_modelo, paragrafos_do_modelo
from .models import Certificate

logger = logging.getLogger(__name__)

MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)

# Chaves de imagem: entram no HTML como data URI, mas não no payload auditado.
_CHAVES_DE_IMAGEM = ("qr",)


class CertificadoIndisponivel(Exception):
    """A matrícula não está aprovada ou o curso não tem modelo de certificado."""


def _data_uri_bytes(conteudo: bytes, extensao: str) -> str:
    return "data:image/%s;base64,%s" % (
        extensao,
        base64.b64encode(conteudo).decode("ascii"),
    )


def _qr_data_uri(url: str) -> str:
    """QR Code (PNG) apontando para a validação pública do certificado."""
    if not url:
        return ""
    import segno

    buffer = io.BytesIO()
    segno.make(url).save(buffer, kind="png", scale=8, border=4)
    return _data_uri_bytes(buffer.getvalue(), "png")


def aplicar_modelo_certificado(curso: Course, conteudo: bytes) -> None:
    """Extrai fundo e texto do DOCX enviado e grava no curso.

    Substitui a extração anterior (arquivo de fundo antigo é removido).
    Levanta :class:`ModeloInvalido` quando o DOCX não é utilizável.
    """
    dados = extrair_modelo(conteudo)
    if curso.certificate_background:
        curso.certificate_background.delete(save=False)
    extensao = dados["extensao"]
    curso.certificate_background.save(
        f"modelo-{curso.pk}.{extensao}",
        ContentFile(dados["imagem"]),
        save=False,
    )
    curso.certificate_text = dados["texto"]


def limpar_modelo_certificado(curso: Course) -> None:
    """Remove o modelo enviado (o curso fica sem modelo de certificado)."""
    if curso.certificate_model:
        curso.certificate_model.delete(save=False)
    if curso.certificate_background:
        curso.certificate_background.delete(save=False)
    # Zera explicitamente: FieldFile.delete deixa o nome como None e o
    # campo precisa ficar em string vazia para o NOT NULL do banco.
    curso.certificate_model = ""
    curso.certificate_background = ""
    curso.certificate_text = ""


def modelo_do_curso_valido(curso: Course) -> bool:
    """O curso tem arte + texto extraídos, prontos para renderizar?"""
    return bool(curso.certificate_background and curso.certificate_text)


def _garantir_modelo_extraido(curso: Course) -> None:
    """Extração lazy: cobre uploads feitos direto no Django admin.

    Se o DOCX estiver presente mas a extração não (ou falhar), permanece
    sem modelo e a emissão é recusada com mensagem clara.
    """
    if not curso.certificate_model or curso.certificate_background:
        return
    try:
        aplicar_modelo_certificado(curso, curso.certificate_model.read())
    except (ModeloInvalido, OSError) as erro:
        logger.warning(
            "Modelo de certificado do curso %s não pôde ser extraído: %s",
            curso.pk,
            erro,
        )
        return
    curso.save(
        update_fields=["certificate_background", "certificate_text"]
    )


def caminho_pdf(codigo: str) -> Path:
    return Path(settings.MEDIA_ROOT) / "certificados" / f"{codigo}.pdf"


def gerar_codigo() -> str:
    """Código de autenticação curto e verificável: ESEM-2026-A1B2C3D4E5F6.

    O prefixo usa o ano de emissão; a parte aleatória tem 48 bits
    (``token_hex(6)``) — colisão praticamente nula em qualquer volume,
    muito acima do que um UUID de 36 caracteres resolveria, mas com
    tamanho digita­vel em papel. A unicidade é garantida pelo banco
    (``unique=True``) + retry da emissão.
    """
    return f"ESEM-{timezone.localdate().year}-{secrets.token_hex(6).upper()}"


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
    validacao = (
        f"{settings.SITE_URL}/validar/?codigo={codigo}" if settings.SITE_URL else ""
    )
    return {
        "aluno": enrollment.medico.nome,
        "curso": curso.title,
        "carga": carga_horaria(segundos),
        "data": f"{hoje.day} de {MESES[hoje.month - 1]} de {hoje.year}",
        "codigo": codigo,
        "percentual": round(course_coverage(enrollment), 1),
        # Link de validação impresso no verso.
        "validacao": validacao,
        "qr": _qr_data_uri(validacao),
    }


def montar_html(enrollment: Enrollment, dados: dict) -> str:
    """Anverso (modelo do curso) + verso de validação.

    Todo curso precisa de modelo: sem arte/texto extraídos a emissão é
    recusada — não existe modelo padrão.
    """
    curso = enrollment.course
    _garantir_modelo_extraido(curso)
    if not modelo_do_curso_valido(curso):
        raise CertificadoIndisponivel(
            "Este curso ainda não tem modelo de certificado cadastrado. "
            "Cadastre o modelo (.docx) no painel antes de emitir."
        )
    extensao = curso.certificate_background.name.rsplit(".", 1)[-1]
    # Cópia local: fundo/paragrafos são só de renderização e não podem
    # vazar para o payload congelado.
    render = dict(dados)
    render["fundo"] = _data_uri_bytes(
        curso.certificate_background.read(), extensao
    )
    render["paragrafos"] = paragrafos_do_modelo(
        curso.certificate_text, enrollment.medico.nome
    )
    return render_to_string("certificates/certificado_modelo.html", render)


def renderizar_html(certificate: Certificate) -> str:
    """HTML do certificado pronto para o WeasyPrint (usado nos testes)."""
    enrollment = (
        Enrollment.objects.select_related("course", "medico")
        .get(pk=certificate.enrollment_id)
    )
    dados = dados_do_certificado(enrollment, certificate.code)
    return montar_html(enrollment, dados)


def gerar_pdf(certificate: Certificate) -> Path:
    """Renderiza o PDF do certificado e arquiva os dados junto dele."""
    enrollment = (
        Enrollment.objects.select_related("course", "medico")
        .get(pk=certificate.enrollment_id)
    )
    dados = dados_do_certificado(enrollment, certificate.code)
    html = montar_html(enrollment, dados)

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
    registrada na auditoria; baixas seguintes reutilizam o mesmo código — e,
    se os dados cadastrais mudaram (ex.: nome corrigido), regeram o PDF com
    o cadastro atual (auditoria ``certificado_reemitido``).
    """
    if enrollment.status != Enrollment.Status.APPROVED:
        raise CertificadoIndisponivel(
            "O certificado só é emitido após a aprovação da matrícula."
        )

    certificate = Certificate.objects.filter(enrollment=enrollment).first()
    if certificate is None:
        # Toda emissão exige o modelo do curso (não há modelo padrão).
        curso = enrollment.course
        _garantir_modelo_extraido(curso)
        if not modelo_do_curso_valido(curso):
            raise CertificadoIndisponivel(
                "Este curso ainda não tem modelo de certificado cadastrado. "
                "Cadastre o modelo (.docx) no painel antes de emitir."
            )
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
    else:
        # Dados cadastrais podem ser corrigidos depois da emissão (nome é o
        # caso mais comum): se o que seria impresso mudou, o download regera
        # o PDF com o cadastro atual. O código de autenticação é o mesmo —
        # links e QRs já emitidos continuam válidos.
        dados = dados_do_certificado(enrollment, certificate.code)
        anterior = certificate.payload or {}
        if (
            dados["aluno"] != anterior.get("aluno")
            or dados["curso"] != anterior.get("curso")
        ):
            gerar_pdf(certificate)
            record_audit(
                "certificado_reemitido",
                actor=actor,
                target=certificate.code,
                detail={
                    "aluno_antes": anterior.get("aluno"),
                    "aluno_agora": dados["aluno"],
                    "curso_antes": anterior.get("curso"),
                    "curso_agora": dados["curso"],
                },
                ip=ip,
            )

    if not caminho_pdf(certificate.code).exists():
        gerar_pdf(certificate)
    return certificate
