"""Cria (ou redefine) a senha de um administrador do portal."""

import secrets

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Cria um administrador do portal com senha gerada."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Nome de usuário do administrador")
        parser.add_argument("--email", default="", help="E-mail do administrador")
        parser.add_argument(
            "--senha",
            default="",
            help="Senha desejada (opcional). Se omitida, uma senha forte é gerada.",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        if not username:
            raise CommandError("Informe o nome de usuário.")

        senha = options["senha"] or secrets.token_urlsafe(12)
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": options["email"], "is_staff": True, "is_superuser": True},
        )
        user.is_staff = True
        user.is_superuser = True
        if options["email"]:
            user.email = options["email"]
        user.set_password(senha)
        user.save()

        self.stdout.write(self.style.SUCCESS("Administrador criado." if created else "Administrador atualizado."))
        self.stdout.write(f"Usuário: {username}")
        self.stdout.write(f"Senha:   {senha}")
        self.stdout.write("Guarde esta senha com segurança (ela não será exibida novamente).")
