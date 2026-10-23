"""Testes do painel administrativo."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Medico
from apps.courses.models import AuditLog, Course, Enrollment, Progress, Video
from apps.courses.services import refresh_enrollment_status
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

    def test_pagina_de_cursos_tem_abas_gerenciar_e_previsualizar(self):
        self.client.force_login(self.admin)
        resposta = self.client.get("/painel/cursos/")
        self.assertContains(resposta, "Gerenciar")
        self.assertContains(resposta, "Pré-visualizar")
        self.assertContains(resposta, "/cursos/")


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


class TestRetirarAprovacao(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin-revoga", "a@b.c", "senha12345")
        user = User.objects.create_user("medico-revoga", password="senha12345")
        self.medico = Medico.objects.create(
            user=user, cpf="33344455566", nome="Medico Revoga", must_change_password=False
        )
        self.curso = Course.objects.create(
            title="Curso revoga", status=Course.Status.ACTIVE, min_percent=50
        )
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula", duration=100
        )
        self.matricula = Enrollment.objects.create(
            medico=self.medico,
            course=self.curso,
            status=Enrollment.Status.APPROVED,
            approved_at=timezone.now(),
            approved_by=self.admin,
        )
        Progress.objects.create(
            enrollment=self.matricula,
            video=self.video,
            covered=100,
            furthest=100,
            last_position=100,
        )
        self.client.force_login(self.admin)

    def test_retira_aprovacao_sem_apagar_progresso(self):
        resposta = self.client.post(
            f"/painel/matriculas/{self.matricula.pk}/acao/",
            {"acao": "retirar_aprovacao"},
        )
        self.assertEqual(resposta.status_code, 302)

        self.matricula.refresh_from_db()
        self.assertEqual(self.matricula.status, Enrollment.Status.APPROVAL_REVOKED)
        self.assertIsNone(self.matricula.approved_at)
        self.assertIsNone(self.matricula.approved_by)
        self.assertEqual(
            Progress.objects.filter(enrollment=self.matricula).count(), 1
        )
        self.assertEqual(
            Progress.objects.get(enrollment=self.matricula).covered, 100
        )

    def test_aprovacao_retirada_nao_e_desfeita_automaticamente(self):
        refresh_enrollment_status(self.matricula)
        self.matricula.refresh_from_db()
        self.assertEqual(self.matricula.status, Enrollment.Status.APPROVED)

        self.client.post(
            f"/painel/matriculas/{self.matricula.pk}/acao/",
            {"acao": "retirar_aprovacao"},
        )
        self.matricula.refresh_from_db()
        refresh_enrollment_status(self.matricula)
        self.matricula.refresh_from_db()
        self.assertEqual(self.matricula.status, Enrollment.Status.APPROVAL_REVOKED)


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


class TestCadastroDeMedicosEmLote(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin-lote", "a@b.c", "senha12345")
        self.client.force_login(self.admin)

    def test_cadastra_varios_medicos_de_uma_vez(self):
        resposta = self.client.post(
            "/painel/medicos/lote/",
            {
                "dados": (
                    "Maria da Silva; 111.222.333-44; PE-1234; maria@exemplo.com; (81) 99999-0000\n"
                    "João Pereira; 555.666.777-88; PE-5678; joao@exemplo.com; (81) 98888-0000"
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
