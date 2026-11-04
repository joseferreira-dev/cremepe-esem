"""Views utilitárias do site (páginas de erro amigáveis)."""

from django.shortcuts import render


def acesso_restrito(request, exception=None):
    """403 humanizado: nenhuma página de erro crua do Django para o usuário.

    Registrado como ``handler403`` em ``config/urls.py`` — cobre todas as
    ``PermissionDenied`` do sistema (visibilidade de curso, token do player,
    regras de upload etc.) com a identidade do portal.
    """
    return render(request, "403.html", status=403)


def pagina_nao_encontrada(request, exception=None):
    """404 responsivo e com a identidade do portal (``handler404``).

    A página padrão do Django não tem viewport — em celular ela renderiza
    em 980px (layout comprimido/rolável). Aqui ela estica corretamente.
    """
    return render(
        request,
        "erro.html",
        {
            "status": 404,
            "titulo": "Página não encontrada",
            "texto": (
                "O endereço pode ter mudado ou o conteúdo não existe mais."
            ),
        },
        status=404,
    )


def erro_interno(request):
    """500 responsivo (``handler500``) — mesma família do 403/404."""
    return render(
        request,
        "erro.html",
        {
            "status": 500,
            "titulo": "Algo não saiu como esperado",
            "texto": (
                "Tente novamente em instantes. Se o problema persistir, "
                "procure a secretaria do CREMEPE."
            ),
        },
        status=500,
    )
