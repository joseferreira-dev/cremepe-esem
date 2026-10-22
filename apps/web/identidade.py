"""Context processors com a identidade visual do portal."""

from .context_processors import icones


def identidade(request):
    return {
        "icons": icones(),
        "nome_instituicao": "CREMEPE",
        "nome_escola": "Escola Superior de Ética Médica",
    }
