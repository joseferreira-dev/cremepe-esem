"""Middlewares de sessao e autenticacao."""

import logging
import re

from django.conf import settings
from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.crypto import constant_time_compare

logger = logging.getLogger(__name__)


def _sessao_autenticada(chave: str) -> bool:
    """A sessao existe no banco E a senha do usuario ainda confere?

    Uma sessao pode existir e mesmo assim estar invalida (por exemplo, depois
    de uma troca de senha). O middleware nao pode escolher essa sessao so
    porque ela existe: o AuthenticationMiddleware a descartaria e o medico
    apareceria deslogado.
    """
    if not chave:
        return False
    store = SessionStore(chave)
    dados = store.load()
    if not dados:
        return False
    usuario_id = dados.get(SESSION_KEY)
    if not usuario_id:
        return False
    usuario = get_user_model().objects.filter(pk=usuario_id, is_active=True).first()
    if usuario is None:
        return False
    return constant_time_compare(
        str(dados.get(HASH_SESSION_KEY) or ""), usuario.get_session_auth_hash()
    )


class SessaoUnicaMiddleware:
    """Resolve cookies de sessao duplicados e aceita token de API.

    Alguns navegadores (ou extensoes/proxies) deixam Cookies `sessionid`
    duplicados para o mesmo host: um antigo, cuja sessao ja nao existe no banco,
    e outro novo, valido. Como o Django le apenas um deles (o primeiro do
    cabecalho), o medico pode ficar "deslogado" mesmo apos um login bem-sucedido.

    Este middleware examina TODAS as chaves `sessionid` enviadas e, se houver
    mais de uma, escolhe a que ainda existe no banco. Se, mesmo assim, nao
    houver sessao autenticada, tenta o **token de API** enviado pelo player
    (necessario em ambientes que reescrevem o cabecalho Cookie).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        nome = settings.SESSION_COOKIE_NAME
        bruto = request.META.get("HTTP_COOKIE", "")
        chaves = re.findall(rf"(?:^|;\s*){re.escape(nome)}=([^;]*)", bruto)

        if len(chaves) > 1:
            unicas = []
            for chave in chaves:
                if chave and chave not in unicas:
                    unicas.append(chave)
            # Prioridade: sessao realmente autenticada > sessao existente >
            # qualquer chave enviada. Sem essa ordem, um cookie antigo (que
            # existe no banco mas tem hash de senha desatualizado) podia ser
            # escolhido e derrubar o login.
            autenticadas = [chave for chave in unicas if _sessao_autenticada(chave)]
            existentes = [
                chave
                for chave in unicas
                if Session.objects.filter(session_key=chave).exists()
            ]
            candidatas = autenticadas or existentes or unicas
            escolhida = candidatas[0]
            trocar = escolhida != request.COOKIES.get(nome)
            if trocar:
                # Refaz a sessao com a chave valida (o store ja foi criado
                # pelo SessionMiddleware com a chave recebida).
                request.session = SessionStore(escolhida)
                request.COOKIES[nome] = escolhida
            # Com duplicatas, reenvia o cookie da sessao autenticada para o
            # navegador convergir para uma unica chave.
            if trocar or escolhida in autenticadas:
                request.session.modified = True

        # Token de API: tem prioridade sobre o cookie. Isso e essencial quando
        # o navegador mantem, por exemplo, uma sessao de medico no cookie e o
        # painel envia um token de administrador no upload. A sessao e trocada
        # aqui; o AuthenticationMiddleware (que roda depois) resolve o usuario.
        from .tokens import autenticar_por_token, token_do_request, validar_token_api

        dados = validar_token_api(token_do_request(request))
        if dados:
            autenticar_por_token(request, *dados)
        autenticada = _sessao_autenticada(request.session.session_key)

        if not autenticada:
            if request.path.startswith("/api/uploads/"):
                logger.warning(
                    "upload sem autenticacao: path=%s query_token=%s "
                    "header_token=%s auth_header=%s cookies=%s referer=%s",
                    request.path,
                    bool(request.GET.get("api_token")),
                    bool(request.headers.get("X-API-Token")),
                    bool(request.headers.get("Authorization")),
                    sorted(request.COOKIES),
                    request.headers.get("Referer", ""),
                )
            # O SessionMiddleware do Django apaga o cookie quando a sessao
            # esta vazia. Numa aba antiga isso pode remover o cookie valido
            # que o navegador usa em outra aba e derrubar o login. A limpeza
            # explicita acontece apenas no logout.
            request.COOKIES.pop(nome, None)

        return self.get_response(request)


class MustChangePasswordMiddleware:
    """Exige a troca da senha provisoria no primeiro acesso."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Em desenvolvimento e possivel desativar a exigencia (variavel de
        # ambiente EXIGIR_TROCA_SENHA). Em producao, deve permanecer ligado.
        if not settings.EXIGIR_TROCA_SENHA:
            return self.get_response(request)

        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and not user.is_staff
            and request.path not in {reverse("password_change"), reverse("logout")}
        ):
            medico = getattr(user, "medico", None)
            if medico is not None and medico.must_change_password:
                return redirect("password_change")
        return self.get_response(request)
