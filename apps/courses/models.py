from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from apps.accounts.models import Medico


class Course(models.Model):
    """Um curso de Ética Médica (uma turma/edição, com suas próprias gravações)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Rascunho"
        ACTIVE = "active", "Ativo"
        ARCHIVED = "archived", "Arquivado"

    title = models.CharField("Título", max_length=200)
    edition = models.CharField("Turma / Edição", max_length=120, blank=True)
    description = models.TextField("Descrição", blank=True)
    cover = models.FileField(
        "Imagem de capa", upload_to="cortes/", blank=True, help_text="PNG ou JPG."
    )
    status = models.CharField(
        "Situação", max_length=20, choices=Status, default=Status.DRAFT
    )
    min_percent = models.DecimalField(
        "Percentual mínimo para aprovação (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("50.00"),
    )
    sequential = models.BooleanField(
        "Liberar os vídeos sequencialmente", default=True
    )
    unlock_threshold = models.DecimalField(
        "Percentual do vídeo anterior necessário para liberar o próximo (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("95.00"),
    )
    deadline = models.DateField("Prazo para conclusão", null=True, blank=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Curso"
        verbose_name_plural = "Cursos"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title}" + (f" — {self.edition}" if self.edition else "")

    @property
    def total_duration(self) -> float:
        return sum(video.duration or 0 for video in self.videos.all())

    @property
    def video_count(self) -> int:
        return self.videos.count()


class Video(models.Model):
    """Um vídeo (gravação) pertencente a um curso."""

    class Status(models.TextChoices):
        EMPTY = "empty", "Sem arquivo"
        UPLOADING = "uploading", "Enviando arquivo"
        QUEUED = "queued", "Na fila de processamento"
        PROCESSING = "processing", "Processando"
        READY = "ready", "Pronto"
        ERROR = "error", "Erro no processamento"

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="videos", verbose_name="Curso"
    )
    order = models.PositiveIntegerField("Ordem de exibição", default=1)
    title = models.CharField("Título", max_length=200)
    duration = models.FloatField("Duração (segundos)", null=True, blank=True)
    original_filename = models.CharField("Arquivo original", max_length=255, blank=True)
    original_path = models.CharField("Caminho do original", max_length=500, blank=True)
    hls_path = models.CharField("Caminho do HLS", max_length=500, blank=True)
    qualities = models.JSONField(
        "Qualidades geradas",
        default=list,
        blank=True,
        help_text="Ex.: [{'name': '720p', 'bandwidth': 2000000, 'resolution': '1280x720'}]",
    )
    poster = models.CharField("Thumb (poster)", max_length=500, blank=True)
    status = models.CharField(
        "Situação", max_length=20, choices=Status, default=Status.EMPTY
    )
    error_message = models.TextField("Mensagem de erro", blank=True)
    is_required = models.BooleanField("Vídeo obrigatório", default=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Vídeo"
        verbose_name_plural = "Vídeos"
        ordering = ["course", "order", "id"]

    def __str__(self) -> str:
        return f"{self.order}. {self.title}"

    @property
    def is_ready(self) -> bool:
        return self.status == self.Status.READY


class Enrollment(models.Model):
    """Matrícula de um médico em um curso."""

    class Status(models.TextChoices):
        ENROLLED = "enrolled", "Matriculado"
        IN_PROGRESS = "in_progress", "Em andamento"
        APPROVED = "approved", "Aprovado"
        REJECTED = "rejected", "Reprovado"
        CANCELLED = "cancelled", "Cancelado"

    medico = models.ForeignKey(
        Medico, on_delete=models.CASCADE, related_name="enrollments", verbose_name="Médico"
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="enrollments", verbose_name="Curso"
    )
    status = models.CharField(
        "Situação", max_length=20, choices=Status, default=Status.ENROLLED
    )
    min_percent = models.DecimalField(
        "Percentual exigido (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("50.00"),
        help_text="Copiado do curso no momento da matrícula.",
    )
    deadline = models.DateField("Prazo", null=True, blank=True)
    approved_at = models.DateTimeField("Aprovado em", null=True, blank=True)
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approvals",
        verbose_name="Aprovado por",
    )
    created_at = models.DateTimeField("Matriculado em", auto_now_add=True)

    class Meta:
        verbose_name = "Matrícula"
        verbose_name_plural = "Matrículas"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["medico", "course"], name="uniq_enrollment_medico_course"
            )
        ]

    def __str__(self) -> str:
        return f"{self.medico.nome} — {self.course.title}"


class Progress(models.Model):
    """Progresso de um médico em um vídeo (matrícula + vídeo)."""

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.CASCADE, related_name="progress_records"
    )
    video = models.ForeignKey(
        Video, on_delete=models.CASCADE, related_name="progress_records"
    )
    last_position = models.FloatField("Última posição (s)", default=0)
    furthest = models.FloatField("Ponto mais avançado (s)", default=0)
    covered = models.FloatField("Tempo coberto (s)", default=0)
    watch_seconds = models.FloatField("Tempo em aula (s)", default=0)
    intervals = models.JSONField(
        "Faixas assistidas",
        default=list,
        blank=True,
        help_text="Lista de [inicio, fim] em segundos.",
    )
    last_seen_at = models.DateTimeField("Última atividade", null=True, blank=True)
    completed_at = models.DateTimeField("Concluído em", null=True, blank=True)
    suspicious = models.PositiveIntegerField("Eventos suspeitos", default=0)
    allowance = models.FloatField(
        "Margem de avanço acumulada (s)",
        default=0,
        help_text="Saldo de tempo liberado para avançar, controlado pelo servidor.",
    )
    last_playing = models.BooleanField("Estava reproduzindo no último heartbeat", default=False)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Progresso"
        verbose_name_plural = "Progressos"
        constraints = [
            models.UniqueConstraint(
                fields=["enrollment", "video"], name="uniq_progress_enrollment_video"
            )
        ]

    def __str__(self) -> str:
        return f"{self.enrollment} — {self.video}"


class WatchSession(models.Model):
    """Sessão de reprodução (auditoria)."""

    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.CASCADE, related_name="sessions"
    )
    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name="sessions")
    started_at = models.DateTimeField("Início", auto_now_add=True)
    ended_at = models.DateTimeField("Fim", null=True, blank=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    user_agent = models.CharField("Navegador", max_length=300, blank=True)
    device = models.CharField("Dispositivo", max_length=120, blank=True)
    watchdog = models.BooleanField(
        "Sessão interrompida (vídeo pulado)", default=False
    )

    class Meta:
        verbose_name = "Sessão de visualização"
        verbose_name_plural = "Sessões de visualização"
        ordering = ["-started_at"]


class AuditLog(models.Model):
    """Registro de ações relevantes (admin e sistema)."""

    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name="Autor",
    )
    action = models.CharField("Ação", max_length=120)
    target = models.CharField("Objeto", max_length=200, blank=True)
    detail = models.JSONField("Detalhes", default=dict, blank=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    created_at = models.DateTimeField("Quando", auto_now_add=True)

    class Meta:
        verbose_name = "Registro de auditoria"
        verbose_name_plural = "Registros de auditoria"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_at:%d/%m/%Y %H:%M} {self.action}"
