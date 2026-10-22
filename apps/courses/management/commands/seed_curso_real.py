"""Cria o curso real do 17º Curso de Atualização em Ética e Bioética."""

from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.courses.models import Course, Video

CURSO = {
    "title": "17º Curso de Atualização em Ética e Bioética",
    "edition": "17º Curso — Residência Médica",
    "description": (
        "Curso de Atualização em Ética e Bioética para Residência Médica, "
        "promovido pela Escola Superior de Ética Médica do CREMEPE. "
        "Carga horária de 6 horas, gravadas em dois encontros."
    ),
}

VIDEOS = [
    {"order": 1, "title": "Encontro 1 — Webnário (25 de maio)", "duracao": 14005},
    {"order": 2, "title": "Encontro 2 — Webnário (26 de maio)", "duracao": 9668},
]


class Command(BaseCommand):
    help = "Cadastra o curso real e seus dois vídeos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--capa",
            default="arquivos/banner.jpg",
            help="Caminho local da imagem de capa.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        course, created = Course.objects.get_or_create(
            title=CURSO["title"], edition=CURSO["edition"], defaults=CURSO
        )
        if not created:
            course.description = CURSO["description"]
            course.status = Course.Status.ACTIVE
            course.save()

        capa = options["capa"]
        try:
            with open(capa, "rb") as arquivo:
                course.cover.save(
                    "capa-17-curso.jpg", File(arquivo), save=True
                )
            self.stdout.write("Capa atualizada.")
        except OSError as erro:
            self.stdout.write(f"Capa não aplicada: {erro}")

        for item in VIDEOS:
            video, criado = Video.objects.get_or_create(
                course=course,
                order=item["order"],
                defaults={"title": item["title"]},
            )
            if criado:
                self.stdout.write(f"Vídeo criado: {video.title}")
            else:
                self.stdout.write(f"Vídeo já existe: {video.title}")

        self.stdout.write(self.style.SUCCESS(f"Curso #{course.id} pronto."))
        self.stdout.write("ADMIN_URL=/api/uploads/pagina/")
        self.stdout.write(
            f"UPLOAD_VIDEO_IDS={[v.id for v in course.videos.all()]}"
        )
