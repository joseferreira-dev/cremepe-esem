from django.contrib.auth.models import User
from django.db import models

from apps.courses.models import Enrollment


class Certificate(models.Model):
    """Certificado emitido para uma matrícula aprovada.

    O PDF é gerado uma vez e arquivado em ``media/certificados/``. Os dados
    congelados em ``payload`` permitem auditar o que foi impresso mesmo que
    nome, curso ou percentuais mudem depois da emissão.
    """

    enrollment = models.OneToOneField(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="certificate",
        verbose_name="Matrícula",
    )
    code = models.CharField("Código de autenticação", max_length=30, unique=True)
    issued_at = models.DateTimeField("Emitido em", auto_now_add=True)
    issued_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificados_emitidos",
        verbose_name="Emitido por",
        help_text="Vazio quando o próprio aluno emitiu.",
    )
    pdf_path = models.CharField("Arquivo PDF", max_length=500, blank=True)
    payload = models.JSONField(
        "Dados impressos",
        default=dict,
        blank=True,
        help_text="Cópia dos valores que entraram no PDF no momento da emissão.",
    )

    class Meta:
        verbose_name = "Certificado"
        verbose_name_plural = "Certificados"
        ordering = ["-issued_at"]

    def __str__(self) -> str:
        return f"Certificado {self.code}"

    @property
    def aluno(self) -> str:
        return self.enrollment.medico.nome

    @property
    def curso(self) -> str:
        return self.enrollment.course.title
