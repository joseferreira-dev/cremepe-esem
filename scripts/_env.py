"""Leitura de credenciais de desenvolvimento a partir do ambiente."""

from __future__ import annotations

import os
import sys


def exigir(nome: str) -> str:
    """Retorna a variavel de ambiente ou encerra com uma mensagem clara."""
    valor = (os.environ.get(nome) or "").strip()
    if not valor:
        print(
            f"Defina a variavel de ambiente {nome} antes de rodar este script.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return valor
