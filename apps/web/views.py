"""Views utilitárias do site (páginas de erro amigáveis)."""

from django.shortcuts import render


def acesso_restrito(request, exception=None):
    """403 humanizado: nenhuma página de erro crua do Django para o usuário.

    Registrado como ``handler403`` em ``config/urls.py`` — cobre todas as
    ``PermissionDenied`` do sistema (visibilidade de curso, token do player,
    regras de upload etc.) com a identidade do portal.
    """
    return render(request, "403.html", status=403)
