"""Testes da regra de senha inicial padrão do CREMEPE."""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from apps.accounts.models import Medico, normalize_cpf, senha_padrao
from apps.accounts.services import criar_conta_medico, redefinir_senha
from apps.courses.models import Course, Enrollment, Video


class TestSenhaPadrao(TestCase):
    def test_primeiro_nome_sem_acento_mais_cpf(self):
        self.assertEqual(
            senha_padrao("José da Silva", "123.456.789-00"),
            "Jose@12345678900",
        )

    def test_remove_acentos_e_mascara_do_cpf(self):
        self.assertEqual(
            senha_padrao("João de Souza", "123.456.789-00"), "Joao@12345678900"
        )
        self.assertEqual(senha_padrao("Ângela Áquila", "11122233344"), "Angela@11122233344")

    def test_primeira_letra_maiuscula(self):
        self.assertEqual(
            senha_padrao("maria da silva", "11111111111"), "Maria@11111111111"
        )

    def test_nome_composto_usa_apenas_o_primeiro(self):
        self.assertEqual(
            senha_padrao("Maria das Graças", "22222222222"), "Maria@22222222222"
        )

    def test_normaliza_cpf(self):
        self.assertEqual(normalize_cpf("123.456.789-00"), "12345678900")


class TestContaDoMedico(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="medico-12345678900")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="12345678900",
            nome="José da Silva",
            must_change_password=False,
        )

    def test_conta_nova_usa_senha_padrao_e_exige_troca(self):
        senha = criar_conta_medico(self.medico)
        self.assertEqual(senha, "Jose@12345678900")
        self.assertTrue(self.medico.user.check_password("Jose@12345678900"))
        self.assertTrue(self.medico.must_change_password)

    def test_senha_explicita_nao_exige_troca(self):
        senha = criar_conta_medico(self.medico, "OutraSenha@123")
        self.assertEqual(senha, "OutraSenha@123")
        self.assertFalse(self.medico.must_change_password)

    def test_redefinir_senha_volta_ao_padrao(self):
        redefinir_senha(self.medico)
        self.assertTrue(self.medico.user.check_password("Jose@12345678900"))
        self.assertTrue(self.medico.must_change_password)

    def test_nao_cria_usuario_duplicado(self):
        criar_conta_medico(self.medico)
        self.assertEqual(User.objects.filter(username="medico-12345678900").count(), 1)


class TestSessaoERegrasDeAcesso(TestCase):
    """Regras que impedem a 'sessao expirada' espuria."""

    def setUp(self):
        self.user = User.objects.create_user("medico-sessao", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="11122233399",
            nome="Medico Sessao",
            must_change_password=False,
        )

    def test_logout_por_get_nao_encerra_a_sessao(self):
        """Um GET em /sair/ (prefetch, link acidental) nao pode derrubar a sessao."""
        self.client.force_login(self.user)
        resposta = self.client.get("/sair/")
        self.assertEqual(resposta.status_code, 302)
        # A sessao continua valida.
        resposta = self.client.get("/cursos/")
        self.assertEqual(resposta.status_code, 200)

    def test_logout_por_post_encerra_a_sessao(self):
        self.client.force_login(self.user)
        resposta = self.client.post("/sair/")
        self.assertEqual(resposta.status_code, 302)
        resposta = self.client.get("/cursos/")
        self.assertEqual(resposta.status_code, 302)

    def test_paginas_autenticadas_nao_sao_cacheadas(self):
        """Sem no-store, o navegador pode reaproveitar um 302 de login antigo."""
        self.client.force_login(self.user)
        for caminho in ("/cursos/", "/entrar/"):
            resposta = self.client.get(caminho)
            cache = resposta.headers.get("Cache-Control", "")
            self.assertIn("no-store", cache, f"{caminho} deveria ser no-store")
            self.assertIn("no-cache", cache, f"{caminho} deveria ser no-cache")

    def test_redirecionamento_para_login_nao_apaga_cookie_de_sessao(self):
        """Apagar o cookie aqui poderia derrubar uma sessao valida em outra aba."""
        from django.conf import settings

        resposta = self.client.get("/cursos/")
        self.assertEqual(resposta.status_code, 302)
        self.assertNotIn(
            settings.SESSION_COOKIE_NAME,
            resposta.cookies,
            "a resposta nao deve apagar o cookie de sessao",
        )

    def test_api_401_nao_apaga_cookie_de_sessao(self):
        """Aba antiga com cookie orfao nao pode derrubar a sessao nova."""
        from django.conf import settings

        resposta = self.client.get(
            "/api/player/state/?enrollment=1&video=1",
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resposta.status_code, 401)
        self.assertNotIn(
            settings.SESSION_COOKIE_NAME,
            resposta.cookies,
            "a resposta 401 nao deve apagar o cookie de sessao",
        )

    def test_redirecionamento_com_cookie_orfao_nao_apaga_cookie(self):
        """O SessionMiddleware nao pode apagar cookie de sessao vazio."""
        from django.conf import settings

        resposta = self.client.get(
            "/cursos/",
            HTTP_COOKIE=f"{settings.SESSION_COOKIE_NAME}=cookie-orfao",
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, resposta.cookies)


class TestCookieDeSessaoDuplicado(TestCase):
    """O navegador pode enviar cookies antigos e novos no mesmo cabecalho."""

    def setUp(self):
        from django.conf import settings

        self.nome = settings.SESSION_COOKIE_NAME
        self.user = User.objects.create_user("medico-dup", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="22233344455",
            nome="Medico Duplicado",
            must_change_password=False,
        )

    def _chave_valida(self):
        from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY
        from django.contrib.sessions.backends.db import SessionStore

        store = SessionStore()
        store[SESSION_KEY] = str(self.user.pk)
        store[BACKEND_SESSION_KEY] = (
            "django.contrib.auth.backends.ModelBackend"
        )
        store[HASH_SESSION_KEY] = self.user.get_session_auth_hash()
        store.create()
        return store.session_key

    def test_api_usa_a_sessao_valida_quando_ha_cookie_orfao(self):
        valida = self._chave_valida()
        orfao = "a1vdmxrddkpx8v9sq8t4mg4lor205ief"
        cabecalho = f"csrftoken=abc; {self.nome}={orfao}; {self.nome}={valida}"

        resposta = self.client.get(
            "/api/player/state/?enrollment=1&video=1",
            headers={"x-requested-with": "XMLHttpRequest"},
            HTTP_COOKIE=cabecalho,
        )
        # Nao pode dar 401: o servidor deve escolher a sessao que existe.
        self.assertNotEqual(resposta.status_code, 401)

    def test_pagina_usa_a_sessao_valida_quando_ha_cookie_orfao(self):
        valida = self._chave_valida()
        orfao = "a1vdmxrddkpx8v9sq8t4mg4lor205ief"
        cabecalho = f"csrftoken=abc; {self.nome}={orfao}; {self.nome}={valida}"

        resposta = self.client.get("/cursos/", HTTP_COOKIE=cabecalho)
        # Com sessao valida o acesso nao deve ser redirecionado para o login.
        self.assertEqual(resposta.status_code, 200)

    def _chave_existente_porem_invalida(self):
        """Sessao que existe no banco, mas com hash de senha desatualizado."""
        from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY
        from django.contrib.sessions.backends.db import SessionStore

        store = SessionStore()
        store[SESSION_KEY] = str(self.user.pk)
        store[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        store[HASH_SESSION_KEY] = "hash-antigo"
        store.create()
        return store.session_key

    def test_sessao_invalida_nao_tem_prioridade_sobre_a_valida(self):
        invalida = self._chave_existente_porem_invalida()
        valida = self._chave_valida()
        cabecalho = f"{self.nome}={invalida}; {self.nome}={valida}"

        resposta = self.client.get("/cursos/", HTTP_COOKIE=cabecalho)
        self.assertEqual(
            resposta.status_code,
            200,
            "o middleware escolheu a sessao antiga (hash invalido) e deslogou",
        )
        cookie = resposta.cookies.get(self.nome)
        self.assertIsNotNone(cookie, "a resposta deveria regravar o cookie correto")
        self.assertEqual(cookie.value, valida)

    def test_sessao_invalida_nao_derruba_a_api(self):
        invalida = self._chave_existente_porem_invalida()
        valida = self._chave_valida()
        cabecalho = f"{self.nome}={invalida}; {self.nome}={valida}"

        resposta = self.client.get(
            "/api/player/state/?enrollment=1&video=1",
            headers={"x-requested-with": "XMLHttpRequest"},
            HTTP_COOKIE=cabecalho,
        )
        self.assertNotEqual(resposta.status_code, 401)


class TestTokenDeApi(TestCase):
    """O player nao pode depender do cookie (proxies/extensoes o reescrevem)."""

    def setUp(self):
        self.user = User.objects.create_user("medico-token", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="33322211100",
            nome="Medico Token",
            must_change_password=False,
        )
        self.curso = Course.objects.create(
            title="Curso", status=Course.Status.ACTIVE
        )
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula 1", duration=100
        )
        self.enrollment = Enrollment.objects.create(
            medico=self.medico, course=self.curso
        )

    def test_token_e_gerado_e_validado(self):
        from apps.accounts.tokens import gerar_token_api, validar_token_api

        token = gerar_token_api(self.user, "chave-de-sessao")
        dados = validar_token_api(token)
        self.assertIsNotNone(dados)
        self.assertEqual(dados[0], self.user.pk)
        self.assertEqual(dados[1], "chave-de-sessao")

    def test_token_adulterado_e_recusado(self):
        from apps.accounts.tokens import gerar_token_api, validar_token_api

        token = gerar_token_api(self.user, "chave")
        partes = token.split(".")
        partes[-1] = "0" * 64
        self.assertIsNone(validar_token_api(".".join(partes)))

    def test_token_expirado_e_recusado(self):
        from apps.accounts.tokens import gerar_token_api, validar_token_api

        token = gerar_token_api(self.user, "chave", validade=-10)
        self.assertIsNone(validar_token_api(token))

    def test_api_aceita_o_token_sem_cookie_de_sessao(self):
        from apps.accounts.tokens import gerar_token_api

        token = gerar_token_api(self.user, "chave-de-sessao")
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}&video={self.video.id}",
            headers={
                "x-requested-with": "XMLHttpRequest",
                "authorization": f"Bearer {token}",
            },
            HTTP_COOKIE="sessionid=cookie-antigo-que-nao-existe",
        )
        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        self.assertEqual(dados["video_id"], self.video.id)

    def test_api_aceita_token_com_o_cookie_cremepe_sid_orfao(self):
        """Cenario real do Chrome: aba antiga envia cremepe_sid inexistente."""
        from django.conf import settings
        from apps.accounts.tokens import gerar_token_api

        token = gerar_token_api(self.user, "chave-de-sessao")
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}&video={self.video.id}",
            headers={
                "x-requested-with": "XMLHttpRequest",
                "authorization": f"Bearer {token}",
            },
            HTTP_COOKIE=f"{settings.SESSION_COOKIE_NAME}=cookie-orfao-inexistente",
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["video_id"], self.video.id)

    def test_api_aceita_token_quando_a_sessao_do_cookie_esta_invalida(self):
        """Cookie existente com hash antigo nao pode impedir o token valido."""
        from django.conf import settings
        from django.contrib.auth import (
            BACKEND_SESSION_KEY,
            HASH_SESSION_KEY,
            SESSION_KEY,
        )
        from django.contrib.sessions.backends.db import SessionStore
        from apps.accounts.tokens import gerar_token_api

        store = SessionStore()
        store[SESSION_KEY] = str(self.user.pk)
        store[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        store[HASH_SESSION_KEY] = "hash-antigo"
        store.create()

        token = gerar_token_api(self.user, "outra-chave")
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}&video={self.video.id}",
            headers={
                "x-requested-with": "XMLHttpRequest",
                "authorization": f"Bearer {token}",
            },
            HTTP_COOKIE=f"{settings.SESSION_COOKIE_NAME}={store.session_key}",
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["video_id"], self.video.id)

    def test_api_401_com_cookie_cremepe_sid_orfao_nao_apaga_cookie(self):
        from django.conf import settings

        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}&video={self.video.id}",
            headers={"x-requested-with": "XMLHttpRequest"},
            HTTP_COOKIE=f"{settings.SESSION_COOKIE_NAME}=cookie-orfao-inexistente",
        )
        self.assertEqual(resposta.status_code, 401)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, resposta.cookies)

    def test_api_sem_cookie_e_sem_token_responde_401(self):
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}&video={self.video.id}",
            headers={"x-requested-with": "XMLHttpRequest"},
            HTTP_COOKIE="sessionid=cookie-antigo-que-nao-existe",
        )
        self.assertEqual(resposta.status_code, 401)

    def test_api_aceita_token_pela_query_string(self):
        """Proxies que removem headers nao impedem o player (fallback)."""
        from apps.accounts.tokens import gerar_token_api

        token = gerar_token_api(self.user, "chave-de-sessao")
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}"
            f"&video={self.video.id}&api_token={token}",
            headers={"x-requested-with": "XMLHttpRequest"},
            HTTP_COOKIE="sessionid=cookie-antigo-que-nao-existe",
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["video_id"], self.video.id)

    def test_api_ignora_authorization_expirado_quando_query_e_valida(self):
        """Extensao/proxy pode injetar um Bearer antigo; o token novo vence."""
        from django.conf import settings
        from apps.accounts.tokens import gerar_token_api

        novo = gerar_token_api(self.user, "chave-de-sessao")
        antigo = gerar_token_api(self.user, "chave-de-sessao", validade=-3600)
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}"
            f"&video={self.video.id}&api_token={novo}",
            headers={
                "x-requested-with": "XMLHttpRequest",
                "authorization": f"Bearer {antigo}",
            },
            HTTP_COOKIE=f"{settings.SESSION_COOKIE_NAME}=cookie-orfao-inexistente",
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["video_id"], self.video.id)

    def test_api_usa_authorization_valido_se_a_query_estiver_invalida(self):
        from apps.accounts.tokens import gerar_token_api

        valido = gerar_token_api(self.user, "chave-de-sessao")
        invalido = gerar_token_api(self.user, "chave-de-sessao", validade=-3600)
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}"
            f"&video={self.video.id}&api_token={invalido}",
            headers={
                "x-requested-with": "XMLHttpRequest",
                "authorization": f"Bearer {valido}",
                "x-api-token": invalido,
            },
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["video_id"], self.video.id)

    def test_rota_token_player_entrega_token_valido_pela_sessao(self):
        from apps.accounts.tokens import validar_token_api

        self.client.force_login(self.user)
        resposta = self.client.get(
            f"/cursos/{self.enrollment.id}/token-player/",
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resposta.status_code, 200)
        dados = validar_token_api(resposta.json()["api_token"])
        self.assertIsNotNone(dados)
        self.assertEqual(dados[0], self.user.pk)

    def test_rota_token_player_exige_login(self):
        resposta = self.client.get(f"/cursos/{self.enrollment.id}/token-player/")
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/entrar/", resposta["Location"])

    def test_rota_token_player_usa_sessao_valida_com_cookie_orfao(self):
        """A pagina autentica pelo cookie valido; a API recebe o orfao."""
        from django.conf import settings
        from django.contrib.auth import (
            BACKEND_SESSION_KEY,
            HASH_SESSION_KEY,
            SESSION_KEY,
        )
        from django.contrib.sessions.backends.db import SessionStore
        from apps.accounts.tokens import validar_token_api

        store = SessionStore()
        store[SESSION_KEY] = str(self.user.pk)
        store[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        store[HASH_SESSION_KEY] = self.user.get_session_auth_hash()
        store.create()
        cabecalho = (
            f"{settings.SESSION_COOKIE_NAME}=cookie-orfao-inexistente; "
            f"{settings.SESSION_COOKIE_NAME}={store.session_key}"
        )

        resposta = self.client.get(
            f"/cursos/{self.enrollment.id}/token-player/",
            headers={"x-requested-with": "XMLHttpRequest"},
            HTTP_COOKIE=cabecalho,
        )
        self.assertEqual(resposta.status_code, 200)
        dados = validar_token_api(resposta.json()["api_token"])
        self.assertIsNotNone(dados)
        self.assertEqual(dados[0], self.user.pk)

    def test_rota_token_player_recusa_matricula_de_outro_usuario(self):
        outro = User.objects.create_user("outro-token", password="senha12345")
        Medico.objects.create(
            user=outro,
            cpf="33322211101",
            nome="Outro Medico",
            must_change_password=False,
        )
        self.client.force_login(outro)
        resposta = self.client.get(f"/cursos/{self.enrollment.id}/token-player/")
        self.assertEqual(resposta.status_code, 403)


class TestExigenciaDeTrocaDeSenha(TestCase):
    """A troca no primeiro acesso pode ser desativada em desenvolvimento."""

    def setUp(self):
        self.user = User.objects.create_user("medico-troca", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="12345678901",
            nome="José da Silva",
            must_change_password=True,
        )
        self.client.post(
            "/entrar/",
            {"cpf": "12345678901", "senha": "senha12345"},
        )

    @override_settings(EXIGIR_TROCA_SENHA=True)
    def test_com_exigencia_ativa_redireciona_para_troca(self):
        resposta = self.client.get("/cursos/")
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/trocar-senha/", resposta["Location"])

    @override_settings(EXIGIR_TROCA_SENHA=False)
    def test_com_exigencia_desativada_acessa_normalmente(self):
        resposta = self.client.get("/cursos/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Meus cursos")

    @override_settings(EXIGIR_TROCA_SENHA=False)
    def test_login_vai_direto_para_os_cursos(self):
        resposta = self.client.post(
            "/entrar/",
            {"cpf": "12345678901", "senha": "senha12345"},
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertNotIn("/trocar-senha/", resposta["Location"])
