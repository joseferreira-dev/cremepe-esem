"""Testes do painel administrativo."""

import json

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.accounts.models import Medico
from apps.courses.models import AuditLog, Course, Enrollment, Progress, Video
from apps.panel.forms import MedicoForm


class TestAcessoAoPainel(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@b.c", "senha12345")
        self.medico_user = User.objects.create_user("medico", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.medico_user,
            cpf="12345678901",
            nome="Medico",
            must_change_password=False,
        )

    def test_anônimo_redirecionado_para_login_administrativo(self):
        resposta = self.client.get("/painel/")
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/painel/entrar/", resposta["Location"])

    def test_pagina_de_login_administrativo(self):
        resposta = self.client.get("/painel/entrar/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Acesso restrito")
        self.assertContains(resposta, "bg-marca-gradiente")
        self.assertNotContains(resposta, "bg-painel-gradiente")

    def test_login_administrativo_por_usuario_e_senha(self):
        resposta = self.client.post(
            "/painel/entrar/",
            {"username": "admin", "password": "senha12345"},
        )
        self.assertRedirects(resposta, "/painel/")
        self.assertEqual(self.client.get("/painel/").status_code, 200)

    def test_login_administrativo_respeita_next(self):
        resposta = self.client.post(
            "/painel/entrar/?next=/painel/medicos/",
            {"username": "admin", "password": "senha12345"},
        )
        self.assertRedirects(resposta, "/painel/medicos/")

    def test_gestor_staff_entra_pelo_login_administrativo(self):
        User.objects.create_user(
            "gestor-login", password="senha12345", is_staff=True
        )
        resposta = self.client.post(
            "/painel/entrar/",
            {"username": "gestor-login", "password": "senha12345"},
        )
        self.assertRedirects(resposta, "/painel/")
        self.assertEqual(self.client.get("/painel/").status_code, 200)

    def test_medico_nao_entra_pelo_login_administrativo(self):
        resposta = self.client.post(
            "/painel/entrar/",
            {"username": "medico", "password": "senha12345"},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "não tem acesso ao painel administrativo")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_do_painel_volta_para_login_administrativo(self):
        self.client.force_login(self.admin)
        resposta = self.client.post("/painel/sair/")
        self.assertRedirects(resposta, "/painel/entrar/")
        self.assertEqual(self.client.get("/painel/").status_code, 302)

    def test_logout_do_painel_por_get_nao_encerra_sessao(self):
        self.client.force_login(self.admin)
        resposta = self.client.get("/painel/sair/")
        self.assertEqual(resposta.status_code, 405)
        self.assertEqual(self.client.get("/painel/").status_code, 200)

    def test_logout_global_de_admin_volta_para_login_do_painel(self):
        self.client.force_login(self.admin)
        resposta = self.client.post("/sair/")
        self.assertRedirects(resposta, "/painel/entrar/")

    def test_troca_de_senha_de_admin_volta_para_o_painel(self):
        self.client.force_login(self.admin)
        resposta = self.client.post(
            "/trocar-senha/",
            {
                "old_password": "senha12345",
                "new_password1": "NovaSenha@123",
                "new_password2": "NovaSenha@123",
            },
        )
        self.assertRedirects(resposta, "/painel/")

    def test_medico_recebe_403(self):
        self.client.force_login(self.medico_user)
        resposta = self.client.get("/painel/")
        self.assertEqual(resposta.status_code, 403)

    def test_admin_acessa(self):
        self.client.force_login(self.admin)
        resposta = self.client.get("/painel/")
        self.assertEqual(resposta.status_code, 200)

    def test_admin_comum_nao_ve_link_do_django_admin(self):
        gestor = User.objects.create_user(
            "gestor", password="senha12345", is_staff=True
        )
        self.client.force_login(gestor)
        resposta = self.client.get("/painel/")
        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "Django admin (avançado)")
        self.assertContains(resposta, "Sair do painel")

    def test_superuser_nao_ve_atalho_do_django_admin(self):
        self.client.force_login(self.admin)
        resposta = self.client.get("/painel/")
        self.assertNotContains(resposta, "Django admin (avançado)")
        self.assertNotContains(resposta, 'href="/admin/"')

    def test_painel_usa_fundo_com_gradiente(self):
        self.client.force_login(self.admin)
        resposta = self.client.get("/painel/")
        self.assertContains(resposta, "bg-painel-gradiente")


class TestDistribuicaoDoProgresso(TestCase):
    """Regra 1: o card 'Distribuição do progresso' vai para o detalhe do curso."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin-dist", "a@b.c", "senha12345"
        )
        user = User.objects.create_user("medico-dist", password="x")
        self.medico = Medico.objects.create(
            user=user, cpf="11122233300", nome="Medico Dist", must_change_password=False
        )
        self.curso = Course.objects.create(
            title="Curso Dist", status=Course.Status.ACTIVE, min_percent=50
        )
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula", duration=100,
            status=Video.Status.READY,
        )
        self.matricula = Enrollment.objects.create(
            medico=self.medico, course=self.curso, min_percent=50
        )
        Progress.objects.create(
            enrollment=self.matricula, video=self.video,
            covered=60, furthest=60, intervals=[[0, 60]],
        )
        self.client.force_login(self.admin)

    def test_visao_geral_nao_mostra_distribuicao(self):
        html = self.client.get("/painel/").content.decode()
        self.assertNotIn("Distribuição do progresso", html)
        self.assertIn("Progresso por curso", html)

    def test_detalhe_do_curso_mostra_distribuicao(self):
        html = self.client.get(f"/painel/cursos/{self.curso.pk}/").content.decode()
        self.assertIn("Distribuição do progresso", html)
        self.assertIn("50–75%", html)
        self.assertIn('data-tab-target="visao"', html)


class TestGestaoDeAdministradores(TestCase):
    """Regra 2: gestão de usuários do painel restrita ao superusuário."""

    def setUp(self):
        self.super = User.objects.create_superuser(
            "suporte", "a@b.c", "senha12345"
        )
        self.staff = User.objects.create_user(
            "gestor", password="senha12345", is_staff=True
        )
        self.client.force_login(self.super)

    def test_staff_comum_nao_acessa_gestao(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/painel/usuarios/").status_code, 403)
        self.assertEqual(
            self.client.get("/painel/usuarios/novo/").status_code, 403
        )

    def test_superusuário_lista_e_cria(self):
        resposta = self.client.get("/painel/usuarios/")
        self.assertContains(resposta, self.staff.username)

        resposta = self.client.post(
            "/painel/usuarios/novo/",
            {
                "username": "ana.souza",
                "first_name": "Ana",
                "last_name": "Souza",
                "email": "ana@exemplo.com",
                "senha": "SenhaForte@123",
                "is_active": "on",
            },
        )
        self.assertRedirects(resposta, "/painel/usuarios/")
        novo = User.objects.get(username="ana.souza")
        self.assertTrue(novo.is_staff)
        self.assertFalse(novo.is_superuser)
        self.assertTrue(novo.check_password("SenhaForte@123"))

    def test_editar_mantem_staff_sem_superuser(self):
        self.client.post(
            "/painel/usuarios/novo/",
            {
                "username": "carlos",
                "senha": "x",
                "is_active": "on",
            },
        )
        carlos = User.objects.get(username="carlos")
        resposta = self.client.post(
            f"/painel/usuarios/{carlos.pk}/editar/",
            {
                "username": "carlos",
                "first_name": "Carlos",
                "email": "carlos@exemplo.com",
                "senha": "",
                "is_active": "on",
            },
        )
        self.assertRedirects(resposta, "/painel/usuarios/")
        carlos.refresh_from_db()
        self.assertEqual(carlos.first_name, "Carlos")
        self.assertTrue(carlos.is_staff)
        self.assertFalse(carlos.is_superuser)

    def test_nao_edita_superusuario(self):
        resposta = self.client.get(f"/painel/usuarios/{self.super.pk}/editar/")
        self.assertRedirects(resposta, "/painel/usuarios/")

    def test_desativar_bloqueia_acesso(self):
        self.client.post(
            "/painel/usuarios/novo/",
            {"username": "temp", "senha": "x", "is_active": "on"},
        )
        temp = User.objects.get(username="temp")
        self.client.post(
            f"/painel/usuarios/{temp.pk}/acao/", {"acao": "desativar"}
        )
        temp.refresh_from_db()
        self.assertFalse(temp.is_active)

    def test_resetar_senha_gera_nova(self):
        self.client.post(
            "/painel/usuarios/novo/",
            {"username": "redef", "senha": "Antiga@123", "is_active": "on"},
        )
        redef = User.objects.get(username="redef")
        resposta = self.client.post(
            f"/painel/usuarios/{redef.pk}/acao/", {"acao": "resetar_senha"}
        )
        redef.refresh_from_db()
        self.assertFalse(redef.check_password("Antiga@123"))
        self.assertTrue(redef.has_usable_password())


class TestDjangoAdminRestritoAoSuporte(TestCase):
    """Regra 2: /admin/ só para superusuários (suporte de TI)."""

    def setUp(self):
        self.super = User.objects.create_superuser(
            "suporte", "a@b.c", "senha12345"
        )
        self.staff = User.objects.create_user(
            "gestor", password="senha12345", is_staff=True
        )

    def test_staff_nao_acessa_django_admin(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        self.assertIn("/admin/login/", self.client.get("/admin/").url)

    def test_superusuário_acessa_django_admin(self):
        self.client.force_login(self.super)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_staff_acessa_painel(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/painel/").status_code, 200)



class TestFluxoCompleto(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@b.c", "senha12345")
        self.client.force_login(self.admin)

    def test_criar_curso_video_medico_e_matricula(self):
        # Curso
        self.client.post(
            "/painel/cursos/novo/",
            {
                "title": "Ética Médica 2026",
                "edition": "1ª turma",
                "description": "Curso de teste",
                "status": "active",
                "min_percent": "50",
                "sequential": "on",
                "unlock_threshold": "95",
            },
        )
        curso = Course.objects.get(title="Ética Médica 2026")
        self.assertEqual(curso.min_percent, 50)

        # Vídeo
        self.client.post(
            "/painel/videos/novo/",
            {"course": curso.pk, "order": 1, "title": "Aula 1", "is_required": "on"},
        )
        self.assertEqual(curso.videos.count(), 1)

        # Médico
        self.client.post(
            "/painel/medicos/novo/",
            {
                "nome": "Maria da Silva",
                "cpf": "111.222.333-44",
                "crm": "PE-1234",
                "telefone": "",
                "email": "maria@exemplo.org",
                "ativo": "on",
                "senha_provisoria": "",
            },
        )
        medico = Medico.objects.get(cpf="11122233344")
        self.assertTrue(medico.must_change_password)

        # Matrícula
        self.client.post(
            "/painel/matriculas/nova/",
            {
                "medico": medico.pk,
                "course": curso.pk,
                "min_percent": "50",
                "deadline": "",
            },
        )
        matricula = Enrollment.objects.get(medico=medico, course=curso)
        self.assertEqual(matricula.min_percent, 50)

        # Aprovação manual
        self.client.post(
            f"/painel/matriculas/{matricula.pk}/acao/", {"acao": "aprovar"}
        )
        matricula.refresh_from_db()
        self.assertEqual(matricula.status, Enrollment.Status.APPROVED)
        self.assertEqual(matricula.approved_by, self.admin)

        # Auditoria registrou as ações
        acoes = {log.action for log in AuditLog.objects.all()}
        self.assertIn("curso_criado", acoes)
        self.assertIn("aprovacao_manual", acoes)

    def test_matricula_duplicada_e_bloqueada(self):
        curso = Course.objects.create(title="Curso", status=Course.Status.ACTIVE)
        user = User.objects.create_user("m2", password="x")
        medico = Medico.objects.create(
            user=user, cpf="55566677788", nome="Outro", must_change_password=False
        )
        Enrollment.objects.create(medico=medico, course=curso)

        resposta = self.client.post(
            "/painel/matriculas/nova/",
            {"medico": medico.pk, "course": curso.pk, "min_percent": "50", "deadline": ""},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_exportar_csv(self):
        curso = Course.objects.create(title="Curso", status=Course.Status.ACTIVE)
        user = User.objects.create_user("m3", password="x")
        medico = Medico.objects.create(
            user=user, cpf="99988877766", nome="Export", must_change_password=False
        )
        Enrollment.objects.create(medico=medico, course=curso)

        resposta = self.client.get(f"/painel/cursos/{curso.pk}/exportar/")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("text/csv", resposta["Content-Type"])
        corpo = resposta.content.decode("utf-8")
        self.assertIn("Export", corpo)
        self.assertIn("Cobertura %", corpo)

    def test_desativar_medico_bloqueia_acesso(self):
        user = User.objects.create_user("m4", password="x")
        medico = Medico.objects.create(
            user=user, cpf="11122233300", nome="Bloqueado", must_change_password=False
        )
        resposta = self.client.post(
            f"/painel/medicos/{medico.pk}/acao/", {"acao": "desativar"}
        )
        self.assertEqual(resposta.status_code, 302)
        medico.refresh_from_db()
        self.assertFalse(medico.ativo)


class TestFormularioDeMedico(TestCase):
    def test_email_usa_o_estilo_dos_demais_campos(self):
        campo = MedicoForm().fields["email"]
        self.assertEqual(campo.widget.attrs.get("class"), "campo")
        self.assertEqual(campo.widget.attrs.get("placeholder"), "nome@exemplo.com")

    def test_crm_e_telefone_nao_sao_campos_do_cadastro(self):
        campos = MedicoForm().fields
        self.assertNotIn("crm", campos)
        self.assertNotIn("telefone", campos)
        self.assertIn("nome", campos)
        self.assertIn("cpf", campos)
        self.assertIn("email", campos)


class TestOrdemDeVideos(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin-ordem", "a@b.c", "senha12345")
        self.curso = Course.objects.create(
            title="Curso ordem", status=Course.Status.ACTIVE
        )
        self.videos = [
            Video.objects.create(course=self.curso, order=ordem, title=f"Aula {ordem}")
            for ordem in (1, 2, 3)
        ]
        self.client.force_login(self.admin)

    def sequencia(self, curso=None):
        curso = curso or self.curso
        return [(v.title, v.order) for v in curso.videos.order_by("order", "id")]

    def test_novo_video_empurra_os_videos_seguintes(self):
        resposta = self.client.post(
            "/painel/videos/novo/",
            {
                "course": self.curso.pk,
                "order": 2,
                "title": "Aula nova",
                "is_required": "on",
            },
        )
        self.assertEqual(resposta.status_code, 302)

        novo = Video.objects.get(title="Aula nova")
        self.assertEqual(novo.order, 2)
        self.assertEqual(Video.objects.get(title="Aula 1").order, 1)
        self.assertEqual(Video.objects.get(title="Aula 2").order, 3)
        self.assertEqual(Video.objects.get(title="Aula 3").order, 4)

    def test_exclusao_renumera_os_videos_restantes(self):
        aula2 = Video.objects.get(title="Aula 2")
        resposta = self.client.post(f"/painel/videos/{aula2.pk}/excluir/")
        self.assertEqual(resposta.status_code, 302)
        # Sem buraco: o que era 3 vira 2.
        self.assertEqual(
            self.sequencia(), [("Aula 1", 1), ("Aula 3", 2)]
        )

    def test_edicao_mover_para_baixo_ocupa_a_posicao_exata(self):
        aula1 = Video.objects.get(title="Aula 1")
        resposta = self.client.post(
            f"/painel/videos/{aula1.pk}/editar/",
            {"course": self.curso.pk, "order": 3, "title": "Aula 1", "is_required": "on"},
        )
        self.assertEqual(resposta.status_code, 302)
        # Pediu a posição 3 de 3 vídeos: fica em 3 (e não em 2).
        self.assertEqual(
            self.sequencia(), [("Aula 2", 1), ("Aula 3", 2), ("Aula 1", 3)]
        )

    def test_edicao_sem_mudar_ordem_compata_buraco_legado(self):
        # Buraco deixado por uma exclusão antiga (feita fora do painel).
        Video.objects.filter(title="Aula 2").update(order=9)
        aula1 = Video.objects.get(title="Aula 1")
        resposta = self.client.post(
            f"/painel/videos/{aula1.pk}/editar/",
            {"course": self.curso.pk, "order": 1, "title": "Aula 1 editada", "is_required": "on"},
        )
        self.assertEqual(resposta.status_code, 302)
        # Sequência exibida preservada, ordens compactadas para 1..N.
        self.assertEqual(
            self.sequencia(), [("Aula 1 editada", 1), ("Aula 3", 2), ("Aula 2", 3)]
        )

    def test_inclusao_com_buraco_compata_o_curso(self):
        Video.objects.filter(title="Aula 2").update(order=9)
        resposta = self.client.post(
            "/painel/videos/novo/",
            {"course": self.curso.pk, "order": 2, "title": "Nova Aula", "is_required": "on"},
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            self.sequencia(),
            [
                ("Aula 1", 1),
                ("Nova Aula", 2),
                ("Aula 3", 3),
                ("Aula 2", 4),
            ],
        )

    def test_mudar_de_curso_renumera_a_origem_e_o_destino(self):
        destino = Course.objects.create(title="Curso destino", status=Course.Status.ACTIVE)
        Video.objects.create(course=destino, order=1, title="Do destino")
        aula3 = Video.objects.get(title="Aula 3")
        resposta = self.client.post(
            f"/painel/videos/{aula3.pk}/editar/",
            {"course": destino.pk, "order": 2, "title": "Aula 3", "is_required": "on"},
        )
        self.assertEqual(resposta.status_code, 302)
        # Origem ficou com buraco no lugar do Aula 3: compacta.
        self.assertEqual(self.sequencia(), [("Aula 1", 1), ("Aula 2", 2)])
        self.assertEqual(
            self.sequencia(destino), [("Do destino", 1), ("Aula 3", 2)]
        )


class TestAcoesDeMatricula(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin-acoes", "a@b.c", "senha12345")
        user = User.objects.create_user("medico-acoes", password="senha12345")
        self.medico = Medico.objects.create(
            user=user, cpf="33344455566", nome="Medico Acoes", must_change_password=False
        )
        self.curso = Course.objects.create(
            title="Curso ações", status=Course.Status.ACTIVE, min_percent=50
        )
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula", duration=100
        )
        self.matricula = Enrollment.objects.create(
            medico=self.medico, course=self.curso
        )
        Progress.objects.create(
            enrollment=self.matricula,
            video=self.video,
            covered=100,
            furthest=100,
            last_position=100,
        )
        self.client.force_login(self.admin)

    def test_aprovar_via_ajax_nao_redireciona(self):
        resposta = self.client.post(
            f"/painel/matriculas/{self.matricula.pk}/acao/",
            {"acao": "aprovar"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resposta.status_code, 200)
        dados = json.loads(resposta.content)
        self.assertTrue(dados["ok"])
        self.assertEqual(dados["status"], Enrollment.Status.APPROVED)
        self.assertEqual(dados["status_display"], "Aprovado")
        self.matricula.refresh_from_db()
        self.assertEqual(self.matricula.status, Enrollment.Status.APPROVED)

    def test_reprovar_via_ajax_nao_redireciona(self):
        resposta = self.client.post(
            f"/painel/matriculas/{self.matricula.pk}/acao/",
            {"acao": "reprovar"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resposta.status_code, 200)
        dados = json.loads(resposta.content)
        self.assertTrue(dados["ok"])
        self.assertEqual(dados["status"], Enrollment.Status.REJECTED)
        self.assertEqual(dados["status_display"], "Reprovado")
        self.matricula.refresh_from_db()
        self.assertEqual(self.matricula.status, Enrollment.Status.REJECTED)

    def test_botoes_de_aprovacao_tem_dialog_e_ajax(self):
        html = self.client.get(f"/painel/cursos/{self.curso.pk}/").content.decode()
        self.assertIn('data-ajax="1"', html)
        self.assertIn('data-acao="aprovar"', html)
        self.assertIn('data-acao="reprovar"', html)
        self.assertIn("data-confirm=", html)

    def test_situacao_aprovacao_retirada_foi_removida(self):
        valores = [valor for valor, _ in Enrollment.Status.choices]
        self.assertNotIn("approval_revoked", valores)
        self.assertNotIn("Aprovação retirada", dict(Enrollment.Status.choices).values())


class TestAbasEDialogsDoCurso(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin-abas", "a@b.c", "senha12345")
        self.curso = Course.objects.create(
            title="Curso abas",
            description="Descrição do curso",
            status=Course.Status.ACTIVE,
        )
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula 1", duration=60
        )
        self.client.force_login(self.admin)

    def test_detalhe_do_curso_tem_abas_para_cada_assunto(self):
        resposta = self.client.get(f"/painel/cursos/{self.curso.pk}/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "data-tabs")
        for aba in ("visao", "videos", "matriculados", "configuracoes"):
            self.assertContains(resposta, f'data-tab-target="{aba}"')
        for texto in ("Descrição", "Lista de vídeos", "Matricular médicos", "Configurações"):
            self.assertContains(resposta, texto)

    def test_acoes_destrutivas_usam_modal_estilizado(self):
        for url in (
            f"/painel/cursos/{self.curso.pk}/",
            f"/painel/videos/{self.video.pk}/",
        ):
            html = self.client.get(url).content.decode()
            self.assertIn("data-modal", html)
            self.assertIn("data-confirm=", html)
            self.assertNotIn('onsubmit="return confirm', html)
            self.assertNotIn("confirm(", html)

    def test_listagem_de_cursos_nao_tem_abas_progresso_nem_abrir(self):
        html = self.client.get("/painel/cursos/").content.decode()
        self.assertNotIn("Pré-visualizar", html)
        self.assertNotIn("Progresso médio", html)
        self.assertNotIn("Abrir", html)

    def test_detalhe_do_curso_nao_tem_voltar_ver_matriculados_nem_abrir(self):
        html = self.client.get(f"/painel/cursos/{self.curso.pk}/").content.decode()
        self.assertNotIn("Voltar", html)
        self.assertNotIn("Ver matriculados", html)
        self.assertNotIn("Abrir", html)


class TestCadastroDeMedicosEmLote(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin-lote", "a@b.c", "senha12345")
        self.client.force_login(self.admin)

    def test_cadastra_varios_medicos_de_uma_vez(self):
        resposta = self.client.post(
            "/painel/medicos/lote/",
            {
                "dados": (
                    "Maria da Silva; 111.222.333-44; maria@exemplo.com\n"
                    "João Pereira; 555.666.777-88; joao@exemplo.com"
                )
            },
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "2 aluno(s) cadastrado(s)")
        self.assertContains(resposta, "Maria@11122233344")
        self.assertContains(resposta, "Joao@55566677788")
        self.assertEqual(Medico.objects.count(), 2)
        self.assertEqual(User.objects.filter(username__startswith="medico-").count(), 2)

    def test_cpf_duplicado_cancela_o_lote_inteiro(self):
        user = User.objects.create_user("medico-existente", password="x")
        Medico.objects.create(
            user=user,
            cpf="11122233344",
            nome="Já Existe",
            must_change_password=False,
        )
        resposta = self.client.post(
            "/painel/medicos/lote/",
            {
                "dados": (
                    "Maria da Silva; 111.222.333-44\n"
                    "João Pereira; 555.666.777-88"
                )
            },
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "já existe um médico cadastrado")
        self.assertEqual(Medico.objects.count(), 1)


class TestMatriculasEmLote(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin-matriculas", "a@b.c", "senha12345")
        self.curso = Course.objects.create(
            title="Curso matrículas", status=Course.Status.ACTIVE, min_percent=50
        )
        self.medicos = []
        for indice in range(1, 4):
            user = User.objects.create_user(f"medico-lote-{indice}", password="x")
            self.medicos.append(
                Medico.objects.create(
                    user=user,
                    cpf=f"1000000000{indice}",
                    nome=f"Médico {indice}",
                    must_change_password=False,
                )
            )
        Enrollment.objects.create(
            medico=self.medicos[0], course=self.curso, min_percent=50
        )
        self.client.force_login(self.admin)

    def test_matricula_varios_medicos_no_mesmo_curso(self):
        resposta = self.client.post(
            f"/painel/cursos/{self.curso.pk}/",
            {
                "acao": "matricular_lote",
                "medicos": [self.medicos[1].pk, self.medicos[2].pk],
                "min_percent": "70",
                "deadline": "",
            },
        )
        self.assertRedirects(resposta, f"/painel/cursos/{self.curso.pk}/")
        self.assertEqual(self.curso.enrollments.count(), 3)
        for medico in self.medicos[1:]:
            self.assertTrue(
                Enrollment.objects.filter(medico=medico, course=self.curso).exists()
            )
        self.assertEqual(
            Enrollment.objects.get(medico=self.medicos[1], course=self.curso).min_percent,
            70,
        )


class TestRemoverMatricula(TestCase):
    """Regra 4: é possível remover matrículas."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin-remover", "a@b.c", "senha12345"
        )
        user = User.objects.create_user("medico-remover", password="x")
        self.medico = Medico.objects.create(
            user=user,
            cpf="44455566677",
            nome="Medico Remover",
            must_change_password=False,
        )
        self.curso = Course.objects.create(
            title="Curso remover", status=Course.Status.ACTIVE, min_percent=50
        )
        self.matricula = Enrollment.objects.create(
            medico=self.medico, course=self.curso, min_percent=50
        )
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula", duration=100,
            status=Video.Status.READY,
        )
        Progress.objects.create(
            enrollment=self.matricula, video=self.video, covered=50, furthest=50
        )
        self.client.force_login(self.admin)

    def test_remover_matricula_apaga_registro_e_progresso(self):
        resposta = self.client.post(
            f"/painel/matriculas/{self.matricula.pk}/acao/",
            {"acao": "remover"},
        )
        self.assertRedirects(resposta, f"/painel/cursos/{self.curso.pk}/")
        self.assertFalse(Enrollment.objects.filter(pk=self.matricula.pk).exists())
        self.assertEqual(Progress.objects.count(), 0)
        self.assertTrue(
            AuditLog.objects.filter(action="matricula_removida").exists()
        )

    def test_botao_remover_aparece_na_turma(self):
        html = self.client.get(f"/painel/cursos/{self.curso.pk}/").content.decode()
        self.assertIn('value="remover"', html)
        self.assertIn("Remover", html)

    def test_remover_da_listagem_de_matriculas(self):
        resposta = self.client.post(
            f"/painel/matriculas/{self.matricula.pk}/acao/",
            {"acao": "remover", "voltar": "/painel/matriculas/"},
        )
        self.assertRedirects(resposta, "/painel/matriculas/")
        self.assertFalse(Enrollment.objects.filter(pk=self.matricula.pk).exists())


class TestCadastroCsv(TestCase):
    """Regra 6: cadastro em lote via arquivo .csv ou colado, separador vírgula."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin-csv", "a@b.c", "senha12345")
        self.client.force_login(self.admin)

    def test_cadastra_via_arquivo_csv(self):
        conteudo = (
            "nome,cpf,e-mail\n"
            "Maria da Silva,11122233344,maria@exemplo.com\n"
            "João Pereira,55566677788,joao@exemplo.com\n"
        ).encode("utf-8")
        arquivo = SimpleUploadedFile("alunos.csv", conteudo, content_type="text/csv")
        resposta = self.client.post("/painel/medicos/lote/", {"arquivo": arquivo})
        self.assertContains(resposta, "2 aluno(s) cadastrado(s)")
        self.assertEqual(Medico.objects.filter(cpf__in=["11122233344", "55566677788"]).count(), 2)

    def test_cadastra_via_dados_colados_com_virgula(self):
        resposta = self.client.post(
            "/painel/medicos/lote/",
            {
                "dados": (
                    "Maria da Silva,11122233344,maria@exemplo.com\n"
                    "João Pereira,55566677788,joao@exemplo.com"
                )
            },
        )
        self.assertContains(resposta, "2 aluno(s) cadastrado(s)")
        self.assertEqual(Medico.objects.count(), 2)

    def test_aceita_ponto_e_virgula_do_excel(self):
        resposta = self.client.post(
            "/painel/medicos/lote/",
            {"dados": "Maria da Silva; 11122233344; maria@exemplo.com"},
        )
        self.assertContains(resposta, "1 aluno(s) cadastrado(s)")

    def test_csv_com_cpf_invalido_rejeitado(self):
        arquivo = SimpleUploadedFile(
            "alunos.csv",
            b"Maria da Silva,123,maria@exemplo.com\n",
            content_type="text/csv",
        )
        resposta = self.client.post("/painel/medicos/lote/", {"arquivo": arquivo})
        self.assertContains(resposta, "CPF deve ter 11 dígitos")
        self.assertEqual(Medico.objects.count(), 0)

    def test_erro_do_lote_aparece_na_pagina(self):
        resposta = self.client.post(
            "/painel/medicos/lote/",
            {"dados": "Maria da Silva,11122233344"},
        )
        # Primeiro cria, depois o mesmo CPF é rejeitado.
        self.assertEqual(Medico.objects.count(), 1)
        resposta2 = self.client.post(
            "/painel/medicos/lote/",
            {"dados": "Maria da Silva,11122233344"},
        )
        self.assertContains(resposta2, "já existe um médico cadastrado")


class TestCamposDeBusca(TestCase):
    """Regra 5: selects de vídeo/matrícula viram campos de busca."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin-busca", "a@b.c", "senha12345"
        )
        self.client.force_login(self.admin)

    def test_formulario_de_video_usa_campo_de_busca(self):
        curso = Course.objects.create(title="Curso Busca", status=Course.Status.ACTIVE)
        html = self.client.get("/painel/videos/novo/").content.decode()
        self.assertIn("data-busca", html)
        self.assertIn("campo-busca-select", html)

    def test_formulario_de_matricula_usa_campo_de_busca(self):
        html = self.client.get("/painel/matriculas/nova/").content.decode()
        self.assertIn("data-busca", html)
        self.assertIn("campo-busca-select", html)

    def test_formulario_de_upload_usa_campo_de_busca(self):
        Video.objects.create(
            course=Course.objects.create(title="C", status=Course.Status.ACTIVE),
            order=1,
            title="Aula",
            status=Video.Status.READY,
        )
        html = self.client.get("/api/uploads/pagina/").content.decode()
        self.assertIn("data-busca", html)
        self.assertIn("substituicao-bloco", html)
