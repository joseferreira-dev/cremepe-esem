"""Worker que processa as tarefas de mídia (transcodificação) em segundo plano."""

import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.courses.models import Video
from apps.media.models import Job
from apps.media.services import limpar_uploads_abandonados, process_transcode


class Command(BaseCommand):
    help = "Executa em laço as tarefas de processamento de mídia (ffmpeg)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Processa apenas a próxima tarefa e sai.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=3.0,
            help="Segundos entre as consultas à fila.",
        )
        parser.add_argument(
            "--limpar",
            action="store_true",
            help="Apenas remove uploads abandonados e sai.",
        )

    def handle(self, *args, **options):
        if options["limpar"]:
            total = limpar_uploads_abandonados()
            self.stdout.write(f"{total} upload(s) abandonado(s) removido(s).")
            return

        self.stdout.write(self.style.SUCCESS("Worker de mídia iniciado."))
        removidos = limpar_uploads_abandonados()
        if removidos:
            self.stdout.write(f"{removidos} upload(s) abandonado(s) removido(s).")
        while True:
            job = self._claim_job()
            if job is None:
                if options["once"]:
                    self.stdout.write("Fila vazia. Encerrando.")
                    return
                time.sleep(options["sleep"])
                continue

            self._process(job)
            if options["once"]:
                return

    def _claim_job(self) -> Job | None:
        with transaction.atomic():
            job = (
                Job.objects.select_for_update(skip_locked=True)
                .filter(status=Job.Status.QUEUED)
                .order_by("created_at")
                .first()
            )
            if job is None:
                return None
            job.status = Job.Status.RUNNING
            job.save(update_fields=["status"])
            return job

    def _process(self, job: Job) -> None:
        video = job.video
        self.stdout.write(f"Processando vídeo #{video.pk} ({video.title})...")
        video.status = Video.Status.PROCESSING
        video.error_message = ""
        video.save(update_fields=["status", "error_message"])

        try:
            result = process_transcode(video)
            video.duration = round(result["duration"] or 0, 2)
            video.qualities = result["qualities"]
            video.hls_path = f"hls/{video.pk}"
            video.poster = f"hls/{video.pk}/poster.jpg"
            video.status = Video.Status.READY
            video.save(
                update_fields=[
                    "duration",
                    "qualities",
                    "hls_path",
                    "poster",
                    "status",
                    "error_message",
                ]
            )
            job.status = Job.Status.DONE
            self.stdout.write(self.style.SUCCESS(f"Vídeo #{video.pk} pronto."))
        except Exception as exc:  # noqa: BLE001
            video.status = Video.Status.ERROR
            video.error_message = str(exc)[:4000]
            video.save(update_fields=["status", "error_message"])
            job.status = Job.Status.FAILED
            job.error = str(exc)[:4000]
            self.stderr.write(self.style.ERROR(f"Falha no vídeo #{video.pk}: {exc}"))
        finally:
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error", "finished_at"])
