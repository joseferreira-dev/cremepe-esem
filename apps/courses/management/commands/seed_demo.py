"""Popula o portal com dados de demonstração (uso em desenvolvimento/testes)."""

import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Medico
from apps.courses.models import Course, Enrollment, Video


class Command(BaseCommand):
    help = "Cria curso, vídeo, médico e matrícula de demonstração."

    def add_arguments(self, parser):
        parser.add_argument("--cpf", default="12345678901")
        parser.add_argument("--senha", default=os.environ.get("MEDICO_SENHA", ""))
        parser.add_argument("--crm", default="PA-12345")

    def handle(self, *args, **options):
        if not options["senha"]:
            raise CommandError("Informe --senha ou defina MEDICO_SENHA.")
        user, _ = User.objects.get_or_create(
            username=f"medico-{options['cpf']}",
            defaults={"first_name": options["cpf"], "last_name": "Teste"},
        )
        user.set_password(options["senha"])
        user.save()

        medico, _ = Medico.objects.update_or_create(
            cpf=options["cpf"],
            defaults={
                "user": user,
                "nome": "Médico de Teste",
                "crm": options["crm"],
                "email": "medico.teste@exemplo.org.br",
                "must_change_password": False,
            },
        )

        course, _ = Course.objects.get_or_create(
            title="Ética Médica — Turma de Teste",
            defaults={
                "edition": "1ª turma 2026",
                "description": "Curso de demonstração.",
                "status": Course.Status.ACTIVE,
            },
        )

        video, created = Video.objects.get_or_create(
            course=course,
            order=1,
            defaults={"title": "Aula 1 — Introdução à Ética Médica"},
        )

        enrollment, _ = Enrollment.objects.get_or_create(
            medico=medico, course=course, defaults={"min_percent": course.min_percent}
        )

        self.stdout.write(self.style.SUCCESS("Dados de demonstração prontos."))
        self.stdout.write(f"CPF: {medico.cpf} | Senha: {options['senha']}")
        self.stdout.write(f"Curso: {course.title} (id={course.id})")
        self.stdout.write(f"Vídeo: {video.title} (id={video.id}, criado={created})")
        self.stdout.write(f"Matrícula: id={enrollment.id}")
