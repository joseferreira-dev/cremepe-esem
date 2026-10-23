import uuid

from django.db import models

from apps.courses.models import Video


class UploadSession(models.Model):
    """Upload de arquivo grande em blocos, com retomada."""

    class Status(models.TextChoices):
        UPLOADING = "uploading", "Enviando"
        COMPLETED = "completed", "Concluído"
        FAILED = "failed", "Falhou"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(
        Video, on_delete=models.CASCADE, related_name="uploads", verbose_name="Vídeo"
    )
    original_filename = models.CharField("Nome do arquivo", max_length=255)
    substituicao = models.CharField(
        "Tipo de substituição",
        max_length=20,
        choices=Video.Substituicao.choices,
        default=Video.Substituicao.NENHUMA,
    )
    total_size = models.BigIntegerField("Tamanho total (bytes)")
    chunk_size = models.IntegerField("Tamanho do bloco (bytes)")
    total_chunks = models.IntegerField("Total de blocos")
    received = models.JSONField("Blocos recebidos", default=list, blank=True)
    status = models.CharField(
        "Situação", max_length=20, choices=Status, default=Status.UPLOADING
    )
    created_at = models.DateTimeField("Iniciado em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Sessão de upload"
        verbose_name_plural = "Sessões de upload"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.video_id})"


class Job(models.Model):
    """Tarefa de processamento em segundo plano."""

    class Kind(models.TextChoices):
        TRANSCODE = "transcode", "Transcodificar"

    class Status(models.TextChoices):
        QUEUED = "queued", "Na fila"
        RUNNING = "running", "Executando"
        DONE = "done", "Concluído"
        FAILED = "failed", "Falhou"

    kind = models.CharField("Tipo", max_length=40, choices=Kind, default=Kind.TRANSCODE)
    video = models.ForeignKey(
        Video, on_delete=models.CASCADE, related_name="jobs", verbose_name="Vídeo"
    )
    status = models.CharField(
        "Situação", max_length=20, choices=Status, default=Status.QUEUED
    )
    progress = models.PositiveIntegerField("Progresso (%)", default=0)
    error = models.TextField("Erro", blank=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    finished_at = models.DateTimeField("Finalizado em", null=True, blank=True)

    class Meta:
        verbose_name = "Tarefa"
        verbose_name_plural = "Tarefas"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} — {self.video} ({self.status})"
