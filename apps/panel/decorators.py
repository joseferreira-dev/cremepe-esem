"""Decoradores de acesso do painel administrativo."""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def somente_admin(view):
    """Exige usuario autenticado **e** privilegio de administrador.

    Nao apaga cookies automaticamente: uma requisicao de aba antiga nao pode
    derrubar a sessao valida que o navegador tem para o portal.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_staff:
            raise PermissionDenied("Acesso restrito à administração do portal.")
        return view(request, *args, **kwargs)

    return wrapper
