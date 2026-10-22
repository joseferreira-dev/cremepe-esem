"""Regras de acesso do médico (conta, senha inicial e ativação)."""

import secrets

from django.contrib.auth.models import User

from .models import Medico, senha_padrao


def criar_conta_medico(medico: Medico, senha: str | None = None) -> str:
    """Cria (ou ajusta) a conta do médico e define a senha inicial.

    Sem ``senha`` explícita, usa a senha padrão do CREMEPE
    (``PrimeiroNome@CPFsemseparadores``) e **exige a troca no primeiro acesso**.
    Devolve a senha utilizada.
    """
    explicita = (senha or "").strip()
    senha = explicita or senha_padrao(medico.nome, medico.cpf)

    if medico.user_id is None:
        user = User.objects.create_user(
            username=f"medico-{medico.cpf}",
            email=medico.email,
            password=senha,
        )
        medico.user = user
    else:
        user = medico.user
        if not user.email and medico.email:
            user.email = medico.email
        user.set_password(senha)
        user.save()

    # Senha padrão do CREMEPE → troca obrigatória no primeiro acesso.
    medico.must_change_password = not bool(explicita)
    return senha


def redefinir_senha(medico: Medico, senha: str | None = None) -> str:
    """Redefine a senha do médico (usada pelo admin no painel)."""
    senha = (senha or "").strip() or senha_padrao(medico.nome, medico.cpf)
    user = medico.user
    user.set_password(senha)
    user.save(update_fields=["password"])
    medico.must_change_password = True
    medico.save(update_fields=["must_change_password"])
    return senha


def senha_provisoria_aleatoria() -> str:
    return secrets.token_urlsafe(9)


# Paths em que um cookie de sessao antigo pode ter ficado no navegador.
# A limpeza automatica foi removida (uma aba antiga nao pode derrubar a sessao
# nova); ela e usada apenas no logout explicito.
PATHS_COOKIE = ("/", "/cursos", "/cursos/", "/entrar", "/entrar/")


def limpar_cookie_sessao(resposta):
    """Apaga o cookie de sessao em todos os paths conhecidos (logout)."""
    from django.conf import settings

    for path in PATHS_COOKIE:
        resposta.delete_cookie(settings.SESSION_COOKIE_NAME, path=path)
    return resposta


def preservar_sessoes(user) -> int:
    """Mantem as sessoes ja abertas validas apos uma troca de senha.

    Usado apenas pelo cenario de DESENVOLVIMENTO: sem isso, redefinir a senha
    do medico encerra a sessao que ele tinha aberta no navegador (comportamento
    padrao e correto do Django, e desejavel em producao).
    """
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    store = SessionStore()
    hash_autorizado = user.get_session_auth_hash()
    atualizadas = 0
    for sessao in Session.objects.filter(expire_date__gt=timezone.now()):
        dados = sessao.get_decoded()
        # O id do usuario fica gravado como texto na sessao.
        if str(dados.get("_auth_user_id")) == str(user.pk):
            dados["_auth_user_hash"] = hash_autorizado
            sessao.session_data = store.encode(dados)
            sessao.save(update_fields=["session_data"])
            atualizadas += 1
    return atualizadas
