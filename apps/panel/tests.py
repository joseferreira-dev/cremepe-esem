"""Testes do painel administrativo."""

from django.contrib.auth.models import User
from django.test import TestCase

from apps.accounts.models import Medico
from apps.courses.models import AuditLog, Course, Enrollment


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

    def test_superuser_ve_link_do_django_admin(self):
        self.client.force_login(self.admin)
        resposta = self.client.get("/painel/")
        self.assertContains(resposta, "Django admin (avançado)")


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
