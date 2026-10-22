"""Dados do médico e regra da senha inicial padrão."""

import unicodedata

from django.contrib.auth.models import User
from django.db import models


def normalize_cpf(value: str) -> str:
    """Remove tudo que não for dígito do CPF."""
    return "".join(char for char in (value or "") if char.isdigit())


def sem_acento(value: str) -> str:
    """Remove os acentos de um texto (ex.: 'José' -> 'Jose')."""
    normalizado = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in normalizado if not unicodedata.combining(char))


def primeiro_nome(nome: str) -> str:
    """Primeiro nome do médico, sem acentos."""
    return sem_acento(nome or "").strip().split(" ")[0]


def senha_padrao(nome: str, cpf: str) -> str:
    """Senha inicial padrão: PrimeiroNome + '@' + CPF sem traços e pontos.

    Ex.: 'José da Silva' / '123.456.789-00' -> 'Jose@12345678900'
    """
    primeiro = primeiro_nome(nome)
    if primeiro:
        primeiro = primeiro[0].upper() + primeiro[1:]
    digitos = normalize_cpf(cpf)
    return f"{primeiro}@{digitos}"


class Medico(models.Model):
    """Médico assistido pelo CREMEPE/ESEM."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="medico", verbose_name="Usuário"
    )
    cpf = models.CharField("CPF", max_length=11, unique=True)
    crm = models.CharField("CRM", max_length=30, blank=True)
    nome = models.CharField("Nome completo", max_length=200)
    email = models.EmailField("E-mail", blank=True)
    telefone = models.CharField("Telefone", max_length=30, blank=True)
    ativo = models.BooleanField("Ativo", default=True)
    must_change_password = models.BooleanField(
        "Precisa trocar a senha no próximo acesso", default=True
    )
    created_at = models.DateTimeField("Cadastrado em", auto_now_add=True)

    class Meta:
        verbose_name = "Médico"
        verbose_name_plural = "Médicos"
        ordering = ["nome"]

    def __str__(self) -> str:
        return f"{self.nome} ({self.cpf})"

    def save(self, *args, **kwargs):
        self.cpf = normalize_cpf(self.cpf)
        super().save(*args, **kwargs)
