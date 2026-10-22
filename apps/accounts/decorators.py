"""Decoradores de acesso."""

from functools import wraps

from django.contrib.auth.views import redirect_to_login


def login_required_limpo(view):
    """Exige sessao ativa sem apagar cookies de sessao.

    O middleware ja escolhe a sessao valida quando o navegador envia chaves
    duplicadas. Apagar o cookie aqui poderia remover uma sessao valida por
    causa de uma aba antiga e derrubar o login sem que o usuario pedisse.
    A limpeza explicita so acontece no logout.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        return view(request, *args, **kwargs)

    return wrapper
