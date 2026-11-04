"""Decoradores de acesso do painel administrativo."""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse


def somente_admin(view):
    """Exige usuario autenticado **e** privilegio de administrador.

    Nao apaga cookies automaticamente: uma requisicao de aba antiga nao pode
    derrubar a sessao valida que o navegador tem para o portal. Quem esta
    logado mas nao e administrador nao vê página de erro: volta para a área
    do aluno com um aviso.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(), login_url=reverse("painel_login")
            )
        if not request.user.is_staff:
            messages.warning(request, "Área restrita à administração do portal.")
            return redirect("my_courses")
        return view(request, *args, **kwargs)

    return wrapper


def somente_superuser(view):
    """Exige superusuário (equipe de suporte de TI).

    Usado para operações sensíveis como a gestão de contas administrativas,
    que define quem pode entrar no painel. Administradores comuns voltam para
    o painel com um aviso, sem página de erro.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(), login_url=reverse("painel_login")
            )
        if not request.user.is_superuser:
            messages.warning(
                request, "Operação exclusiva da equipe de suporte de TI."
            )
            return redirect("painel_index")
        return view(request, *args, **kwargs)

    return wrapper
