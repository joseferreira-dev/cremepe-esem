"""Testes da página do curso (área do aluno)."""

from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from apps.accounts.models import Medico
from apps.courses.models import Course, Enrollment, Video


class TestPaginaDoCurso(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("medico-pagina", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="55544433322",
            nome="Medico Pagina",
            must_change_password=False,
        )
        self.curso = Course.objects.create(
            title="Etica Medica", status=Course.Status.ACTIVE, sequential=True
        )
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula 1", duration=600,
            status=Video.Status.READY,
        )
        self.enrollment = Enrollment.objects.create(
            medico=self.medico, course=self.curso, min_percent=50
        )

    def test_pagina_exige_login(self):
        resposta = self.client.get(f"/cursos/{self.enrollment.id}/")
        self.assertEqual(resposta.status_code, 302)

    def test_medico_nao_acessa_matricula_de_outro(self):
        outro_user = User.objects.create_user("medico-outro", password="x")
        outro = Medico.objects.create(
            user=outro_user, cpf="77788899900", nome="Outro", must_change_password=False
        )
        outra = Enrollment.objects.create(medico=outro, course=self.curso)
        self.client.force_login(self.user)
        resposta = self.client.get(f"/cursos/{outra.id}/")
        self.assertEqual(resposta.status_code, 403)

    def test_pagina_tem_todos_os_elementos_do_player(self):
        """O player aborta em silêncio se faltar qualquer um destes IDs."""
        self.client.force_login(self.user)
        resposta = self.client.get(f"/cursos/{self.enrollment.id}/")
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()

        for elemento in (
            "player",
            "video-list",
            "video-title",
            "player-info",
            "player-warning",
            "watched-fill",
        ):
            self.assertIn(f'id="{elemento}"', html, f"faltou id={elemento}")

        self.assertIn('data-video-id', html)
        self.assertIn('data-unlocked="1"', html)

    def test_configuracao_do_player_vem_antes_do_script(self):
        self.client.force_login(self.user)
        html = self.client.get(f"/cursos/{self.enrollment.id}/").content.decode()
        self.assertIn("window.CREMEPE", html)
        self.assertIn(f"/cursos/{self.enrollment.id}/token-player/", html)
        self.assertIn("/static/js/player", html)
        self.assertLess(
            html.index("window.CREMEPE"),
            html.index("/static/js/player"),
            "window.CREMEPE precisa ser definido antes do player.js",
        )

    def test_player_js_nao_usa_classes_antigas(self):
        """Classes d-none (Bootstrap) não existem mais no design Tailwind."""
        caminho = (
            __import__("pathlib").Path(__file__).resolve().parent.parent.parent
            / "static"
            / "js"
            / "player.js"
        )
        conteudo = caminho.read_text(encoding="utf-8")
        self.assertNotIn("d-none", conteudo)
        self.assertIn("classList.remove('hidden')", conteudo)


class TestPaginaInicial(TestCase):
    """A pagina inicial e publica e fala da ESEM, nao de um curso especifico."""

    def test_pagina_inicial_e_publica(self):
        resposta = self.client.get("/")
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()
        self.assertIn("Escola Superior de Ética Médica", html)
        self.assertIn("Núcleo técnico permanente", html)
        # Nao deve citar o 17o curso (a apresentacao e da ESEM).
        self.assertNotIn("17º", html)
        self.assertNotIn("17&ordm;", html)
        # Deve oferecer o acesso aos cursos.
        self.assertIn("/entrar/", html)

    def test_login_tem_banner_lateral_ao_formulario(self):
        html = self.client.get("/entrar/").content.decode()
        # O banner fica em uma coluna e o formulario em outra.
        self.assertIn("lg:grid-cols-2", html)
        self.assertIn("Banner lateral", html)
        self.assertIn("Formulário", html)

    def test_pagina_inicial_sem_rodape_e_sem_secao_como_funciona(self):
        html = self.client.get("/").content.decode()
        self.assertNotIn("<footer", html)
        self.assertNotIn("Conselho Regional de Medicina de Pernambuco", html)
        self.assertIn("Escola Superior de Ética Médica", html)
        self.assertNotIn("Como funciona", html)

    def test_pagina_inicial_mostra_o_botao_de_acesso_para_visitante(self):
        html = self.client.get("/").content.decode()
        self.assertIn("Acessar os cursos", html)

    def test_pagina_inicial_mostra_o_botao_de_cursos_para_autenticado(self):
        user = User.objects.create_user("medico-inicio", password="senha12345")
        Medico.objects.create(
            user=user,
            cpf="77766655544",
            nome="Medico Inicio",
            must_change_password=False,
        )
        self.client.force_login(user)
        html = self.client.get("/").content.decode()
        self.assertIn("Ir para os meus cursos", html)

    def test_login_nao_cita_curso_especifico(self):
        html = self.client.get("/entrar/").content.decode()
        self.assertIn("Escola Superior de Ética Médica", html)
        self.assertIn("Acesso aos cursos", html)
        self.assertNotIn("17º", html)
        self.assertNotIn("Webnário", html)


class TestPaginaInstitucional(TestCase):
    """A pagina inicial concentra a apresentacao e o conteudo institucional."""

    def test_pagina_inicial_tem_o_conteudo_institucional_completo(self):
        resposta = self.client.get("/")
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()
        for trecho in (
            "Escola Superior de Ética Médica",
            "Núcleo técnico permanente",
            "03/2005",
            "Temas em debate",
            "Integração institucional",
            "Faculdade Pernambucana de Saúde",
            "Universidade Federal de Pernambuco",
            "Eventos",
        ):
            self.assertIn(trecho, html)
        self.assertNotIn("17º", html)
        self.assertNotIn("17&ordm;", html)

    def test_sobre_redireciona_para_a_pagina_inicial(self):
        resposta = self.client.get("/sobre/")
        self.assertRedirects(resposta, "/", status_code=301, target_status_code=200)

    def test_nav_e_rodape_nao_tem_link_sobre(self):
        from django.template.loader import render_to_string

        for template in ("partials/nav.html", "partials/foot.html"):
            html = render_to_string(template, request=None)
            self.assertNotIn("Sobre a ESEM", html, f"{template} nao deveria linkar Sobre")
            self.assertNotIn("/sobre/", html, f"{template} nao deveria linkar /sobre/")


class TestCatalogoDoAdmin(TestCase):
    """O admin ve todos os cursos, mesmo os que nao tem matricula."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin-catalogo", "a@b.c", "senha12345")
        self.user = User.objects.create_user("medico-catalogo", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="88877766655",
            nome="Medico Catalogo",
            must_change_password=False,
        )
        self.curso_com_matricula = Course.objects.create(
            title="Curso com matricula", edition="Turma A", status=Course.Status.ACTIVE
        )
        self.curso_sem_matricula = Course.objects.create(
            title="Curso sem matricula", status=Course.Status.DRAFT
        )
        Enrollment.objects.create(medico=self.medico, course=self.curso_com_matricula)

    def test_admin_ve_todos_os_cursos_com_thumbnails(self):
        self.client.force_login(self.admin)
        html = self.client.get("/cursos/").content.decode()

        self.assertIn("Gerenciar cursos", html)
        self.assertIn("Curso com matricula", html)
        self.assertIn("Curso sem matricula", html)
        self.assertIn("1 matriculado(s)", html)
        self.assertIn("Abrir", html)
        self.assertIn("Editar", html)
        self.assertNotIn("Meus cursos", html)
        self.assertIn(
            f"/painel/cursos/{self.curso_sem_matricula.id}/",
            html,
            "o curso sem matricula tambem precisa abrir no painel",
        )

    def test_medico_continua_vendo_somente_seus_cursos(self):
        self.client.force_login(self.user)
        html = self.client.get("/cursos/").content.decode()

        self.assertIn("Meus cursos", html)
        self.assertIn("Curso com matricula", html)
        self.assertNotIn("Curso sem matricula", html)
        self.assertNotIn("Gerenciar cursos", html)


class TestPaginaDeLogin(TestCase):
    def setUp(self):
        self.url = "/entrar/"

    def test_banner_de_apresentacao_e_campos_com_estilo_do_sistema(self):
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()

        # Banner de apresentação do sistema
        self.assertIn("Escola Superior de Ética Médica", html)
        self.assertIn("Acesso aos cursos", html)
        self.assertIn("bg-marca-gradiente", html)
        self.assertIn("logotipo-cremepe", html)
        self.assertIn("logomarca-esem", html)
        self.assertIn("Educação Médica Continuada", html)
        # Espaço de login
        self.assertIn('name="cpf"', html)
        self.assertIn('name="senha"', html)
        self.assertIn("Entrar no portal", html)
        # Estilo Tailwind (não Bootstrap)
        self.assertIn('class="campo"', html)
        self.assertNotIn("form-control", html)
        # Página enxuta: sem menu nem rodapé repetidos
        self.assertNotIn("Meus cursos", html)
        self.assertNotIn("Painel administrativo", html)

    def test_login_nao_tem_rodape_nem_instrucoes_do_curso(self):
        html = self.client.get(self.url).content.decode()
        self.assertNotIn("<footer", html)
        self.assertNotIn("Conselho Regional de Medicina de Pernambuco", html)
        self.assertNotIn("O conteúdo é liberado", html)
        self.assertNotIn("Sua posição é salva", html)
        self.assertNotIn("A evolução fica registrada", html)

    def test_credenciais_invalidas_mostram_erro(self):
        resposta = self.client.post(
            self.url,
            {"cpf": "00000000000", "senha": "errada", "csrfmiddlewaretoken": "x"},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("CPF ou senha inválidos", resposta.content.decode())


class TestVisibilidadeDoCursoParaAluno(TestCase):
    """Regras 1 e 2: rascunho oculto; arquivado/expirado bloqueado e em cinza."""

    def setUp(self):
        self.user = User.objects.create_user("medico-visib", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="66655544433",
            nome="Medico Visib",
            must_change_password=False,
        )
        self.client.force_login(self.user)

    def _curso(self, **kwargs):
        padrao = {"title": "Curso Teste", "status": Course.Status.ACTIVE}
        padrao.update(kwargs)
        return Course.objects.create(**padrao)

    def _matricular(self, curso, **kwargs):
        padrao = {"min_percent": 50}
        padrao.update(kwargs)
        return Enrollment.objects.create(medico=self.medico, course=curso, **padrao)

    def test_rascunho_nao_aparece_para_aluno(self):
        curso = self._curso(status=Course.Status.DRAFT)
        Video.objects.create(
            course=curso, order=1, title="Aula", duration=100, status=Video.Status.READY
        )
        self._matricular(curso)
        html = self.client.get("/cursos/").content.decode()
        self.assertNotIn(curso.title, html)

    def test_rascunho_nao_pode_ser_acessado(self):
        curso = self._curso(status=Course.Status.DRAFT)
        Video.objects.create(
            course=curso, order=1, title="Aula", duration=100, status=Video.Status.READY
        )
        matricula = self._matricular(curso)
        self.assertEqual(self.client.get(f"/cursos/{matricula.id}/").status_code, 403)

    def test_arquivado_aparece_em_cinza_e_bloqueado(self):
        curso = self._curso(status=Course.Status.ARCHIVED)
        Video.objects.create(
            course=curso, order=1, title="Aula", duration=100, status=Video.Status.READY
        )
        matricula = self._matricular(curso)
        html = self.client.get("/cursos/").content.decode()
        self.assertIn(curso.title, html)
        self.assertIn("grayscale", html)
        self.assertIn("Arquivado", html)
        self.assertNotIn(f'href="/cursos/{matricula.id}/"', html)
        self.assertEqual(self.client.get(f"/cursos/{matricula.id}/").status_code, 403)

    def test_fora_do_prazo_aparece_em_cinza_e_bloqueado(self):
        curso = self._curso(deadline=date(2000, 1, 1))
        Video.objects.create(
            course=curso, order=1, title="Aula", duration=100, status=Video.Status.READY
        )
        matricula = self._matricular(curso)
        html = self.client.get("/cursos/").content.decode()
        self.assertIn(curso.title, html)
        self.assertIn("grayscale", html)
        self.assertIn("Prazo encerrado", html)
        self.assertNotIn(f'href="/cursos/{matricula.id}/"', html)
        self.assertEqual(self.client.get(f"/cursos/{matricula.id}/").status_code, 403)

    def test_prazo_da_matricula_tambem_bloqueia(self):
        curso = self._curso()
        Video.objects.create(
            course=curso, order=1, title="Aula", duration=100, status=Video.Status.READY
        )
        matricula = self._matricular(curso, deadline=date(2000, 1, 1))
        html = self.client.get("/cursos/").content.decode()
        self.assertIn("Prazo encerrado", html)
        self.assertEqual(self.client.get(f"/cursos/{matricula.id}/").status_code, 403)

    def test_ativo_normal_continua_acessivel(self):
        curso = self._curso()
        video = Video.objects.create(
            course=curso, order=1, title="Aula", duration=100, status=Video.Status.READY
        )
        matricula = self._matricular(curso)
        resposta = self.client.get(f"/cursos/{matricula.id}/")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("data-video-id", resposta.content.decode())


class TestDisponibilidadeDeVideo(TestCase):
    """Regra 3: vídeos em envio/processamento ficam ocultos ou mantêm a versão antiga."""

    def setUp(self):
        self.user = User.objects.create_user("medico-videos", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="77788899901",
            nome="Medico Videos",
            must_change_password=False,
        )
        self.curso = Course.objects.create(
            title="Curso Videos", status=Course.Status.ACTIVE
        )
        self.matricula = Enrollment.objects.create(
            medico=self.medico, course=self.curso, min_percent=50
        )
        self.client.force_login(self.user)

    def _pagina(self):
        return self.client.get(f"/cursos/{self.matricula.id}/").content.decode()

    def test_video_novo_em_processamento_nao_aparece(self):
        Video.objects.create(
            course=self.curso, order=1, title="Novo", duration=0,
            status=Video.Status.PROCESSING,
        )
        html = self._pagina()
        self.assertNotIn("data-video-id", html)
        self.assertIn("Nenhum vídeo cadastrado", html)

    def test_substituicao_mantem_versao_antiga_disponivel(self):
        antigo = Video.objects.create(
            course=self.curso, order=1, title="Antigo", duration=100,
            status=Video.Status.PROCESSING, hls_path=f"hls/{self.curso.id}",
        )
        html = self._pagina()
        self.assertIn("data-video-id", html)
        self.assertIn(antigo.title, html)

    def test_video_novo_sem_status_pronto_nao_aparece(self):
        Video.objects.create(
            course=self.curso, order=1, title="Vazio", duration=60,
            status=Video.Status.EMPTY,
        )
        html = self._pagina()
        self.assertNotIn("data-video-id", html)
