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

    O nome é padronizado antes de derivar (mesma regra do cadastro), para
    **criação e reset** convergirem sempre para o mesmo valor — sem a
    correção, um nome em caixa alta geraria "AGUIDA@..." na criação e
    "Aguida@..." no reset.

    Ex.: 'José da Silva' / '123.456.789-00' -> 'Jose@12345678900'
    """
    primeiro = primeiro_nome(nome_padrao(nome))
    if primeiro:
        primeiro = primeiro[0].upper() + primeiro[1:]
    digitos = normalize_cpf(cpf)
    return f"{primeiro}@{digitos}"


# Preposições que ficam minúsculas no meio do nome (padrão do cadastro).
_PREPOSICOES = {"a", "e", "o", "as", "os", "de", "da", "do", "das", "dos"}


def nome_padrao(texto: str) -> str:
    """Padroniza o nome do médico: 'José PAULO DA silva' -> 'José Paulo da Silva'.

    Cada palavra recebe inicial maiúscula (inclusive segmentos com hífen) e as
    preposições comuns permanecem minúsculas, exceto na primeira palavra.
    """
    palavras = (texto or "").split()
    if not palavras:
        return ""
    finais = []
    for indice, palavra in enumerate(palavras):
        if indice > 0 and palavra.lower() in _PREPOSICOES:
            finais.append(palavra.lower())
        else:
            finais.append(
                "-".join(trecho.capitalize() for trecho in palavra.split("-") if trecho)
            )
    return " ".join(finais)


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
        # Padronização do nome no ponto único de gravação (formulários,
        # cadastro em lote, comandos e admin): 'José PAULO DA silva' -> 'José Paulo da Silva'.
        self.nome = nome_padrao(self.nome)
        super().save(*args, **kwargs)
