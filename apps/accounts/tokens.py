"""Token de API para o player (independente de cookies).

Ambientes corporativos/institucionais às vezes têm componente (extensão de
navegador, proxy local, antivírus com inspeção de HTTPS) que reescreve o
cabeçalho ``Cookie`` das requisições feitas por JavaScript. Nesses casos, o
login funciona na navegação normal, mas as chamadas ``fetch`` do player chegam
ao servidor sem o cookie de sessão.

Para blindar o portal, a página autenticada entrega ao player um **token
assinado** que carrega a identidade do usuário e a chave da sessão. O player o
envia no cabeçalho ``Authorization`` e o servidor o valida pela assinatura
(HMAC com o SECRET_KEY) — dispensando o cookie.
"""

import hashlib
import hmac
import time

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore


def gerar_token_api(user, session_key: str | None = None, validade: int = 3600) -> str:
    """Token assinado: ``usuario.sessao.expiracao.assinatura``."""
    expira = int(time.time()) + validade
    payload = f"{user.pk}.{session_key or ''}.{expira}"
    assinatura = hmac.new(
        settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{assinatura}"


def validar_token_api(token: str) -> tuple[int, str] | None:
    """Valida a assinatura e a validade. Devolve (usuario_id, session_key)."""
    if not token:
        return None
    try:
        usuario_id, session_key, expira, assinatura = token.split(".", 3)
        expira = int(expira)
    except (ValueError, TypeError):
        return None

    if expira < time.time():
        return None

    payload = f"{usuario_id}.{session_key}.{expira}"
    esperada = hmac.new(
        settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(assinatura, esperada):
        return None

    try:
        return int(usuario_id), session_key
    except (TypeError, ValueError):
        return None


def token_do_request(request) -> str:
    """Escolhe o primeiro token VALIDO entre query, X-API-Token e Authorization.

    Extensoes/proxies podem injetar um Authorization antigo (expirado) mesmo
    quando o player envia um token novo na URL. Retornar o primeiro token
    presente fazia a requisicao falhar; aqui um token valido sempre vence.
    """
    candidatos: list[str] = []
    query = request.GET.get("api_token", "").strip()
    if query:
        candidatos.append(query)
    cabecalho_api = request.headers.get("X-API-Token", "").strip()
    if cabecalho_api:
        candidatos.append(cabecalho_api)
    autorizacao = request.headers.get("Authorization", "")
    if autorizacao.lower().startswith("bearer "):
        bearer = autorizacao[7:].strip()
        if bearer:
            candidatos.append(bearer)

    for token in candidatos:
        if validar_token_api(token):
            return token
    for token in candidatos:
        if token:
            return token
    return ""


def autenticar_por_token(request, usuario_id: int, session_key: str):
    """Restaura uma sessao valida a partir do token e devolve o usuario."""
    from django.contrib.auth import get_user_model

    usuario_model = get_user_model()
    usuario = usuario_model.objects.filter(pk=usuario_id, is_active=True).first()
    if usuario is None:
        return None

    store = SessionStore(session_key) if session_key else SessionStore()
    if not store.exists(session_key) if session_key else True:
        store = SessionStore()
        store[SESSION_KEY] = str(usuario.pk)
        store[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        store[HASH_SESSION_KEY] = usuario.get_session_auth_hash()
        store.create()
    else:
        store[SESSION_KEY] = str(usuario.pk)
        store[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        store[HASH_SESSION_KEY] = usuario.get_session_auth_hash()
        store.save()

    request.session = store
    return usuario
