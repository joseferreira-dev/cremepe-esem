"""Testes do painel administrativo."""

import json

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.accounts.models import Medico
from apps.courses.models import AuditLog, Course, Enrollment, Progress, Video
from apps.panel.forms import MedicoForm


def processar_lote(client, resposta):
    """Executa os pedaços do cadastro em lote até concluir (o que o JS faz).

    Devolve (criados, ignorados) a partir dos JSONs parciais.
    """
    if resposta.context is not None and "lote_total" not in resposta.context:
        raise AssertionError("a página não expôs lote_total (UI de progresso)")
    criados = []
    ignorados = 0
    for _ in range(100):  # trava de segurança contra laço infinito
        parcial = client.post("/painel/medicos/lote/parcial/")
        if parcial.status_code != 200:
            raise AssertionError(f"pedaço devolveu {parcial.status_code}")
        dados = parcial.json()
        criados.extend(dados["criados"])
        ignorados += dados["ignorados"]
        if dados["processados"] >= dados["total"]:
            return criados, ignorados
    raise AssertionError("o lote não terminou dentro do limite de pedaços")


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

    def test_login_auditado(self):
        """Entrada bem-sucedida e tentativas que falham ficam na auditoria."""
        self.client.post(
            "/painel/entrar/", {"username": "admin", "password": "senha12345"}
        )
        self.assertTrue(
            AuditLog.objects.filter(action="login_sucesso", target="admin").exists()
        )
        self.client.logout()

        self.client.post(
            "/painel/entrar/", {"username": "admin", "password": "errada"}
        )
        falha = AuditLog.objects.filter(action="login_falha", target="admin")
        self.assertEqual(falha.count(), 1)
        self.assertEqual(falha.first().detail.get("motivo"), "credenciais")

        # Conta existente sem perfil de admin também é registrada.
        self.client.post(
            "/painel/entrar/", {"username": "medico", "password": "senha12345"}
        )
        sem_permissao = AuditLog.objects.filter(action="login_falha", target="medico")
        self.assertEqual(sem_permissao.count(), 1)
        self.assertEqual(
            sem_permissao.first().detail.get("motivo"), "conta_sem_permissao"
        )

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

    def test_medico_elevado_para_os_cursos_com_aviso(self):
        """Sem página de erro: o médico volta para a área dele."""
        self.client.force_login(self.medico_user)
        resposta = self.client.get("/painel/")
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/cursos/", resposta.url)
        pagina = self.client.get(resposta.url)
        self.assertContains(pagina, "Área restrita à administração")

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
            medico=self.medico, course=self.curso
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
        # Sem página de erro: volta para o painel com aviso.
        self.client.force_login(self.staff)
        self.assertRedirects(
            self.client.get("/painel/usuarios/"), "/painel/"
        )
        self.assertRedirects(
            self.client.get("/painel/usuarios/novo/"), "/painel/"
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

    def test_nao_promove_conta_de_medico_para_administrativa(self):
        """Separação de contas: painel e médico são acessos distintos."""
        user = User.objects.create_user("medico-gestao", password="senha12345")
        Medico.objects.create(user=user, cpf="77788899901", nome="Medico Gestao")
        resposta = self.client.post(
            f"/painel/usuarios/{user.pk}/editar/",
            {
                "username": "medico-gestao",
                "first_name": "Medico",
                "last_name": "Gestao",
                "email": "",
                "senha": "",
                "is_active": "on",
            },
        )
        # Formulário recusado: a página volta com o erro, sem promover a conta.
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "pertence a um médico")
        user.refresh_from_db()
        self.assertFalse(user.is_staff)

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
                "description": "Curso de teste",
                "status": "active",
                "min_percent": "50",
                "sequential": "on",
                "unlock_threshold": "95",
            },
        )
        curso = Course.objects.get(title="Ética Médica 2026")
        self.assertEqual(curso.min_percent, 50)

        # O aviso de sucesso aparece UMA vez só (o base.html não duplica o
        # aviso que o painel já renderiza na coluna de conteúdo).
        pagina = self.client.get(f"/painel/cursos/{curso.pk}/")
        aviso = "Curso “Ética Médica 2026” salvo."
        self.assertEqual(pagina.content.decode().count(aviso), 1)

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

        # Matrícula (percentual e prazo vêm do curso) → volta para a listagem
        resposta = self.client.post(
            "/painel/matriculas/nova/",
            {
                "medico": medico.pk,
                "course": curso.pk,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], "/painel/matriculas/")
        matricula = Enrollment.objects.get(medico=medico, course=curso)
        from apps.courses.services import criterios_de_aprovacao

        self.assertEqual(criterios_de_aprovacao(matricula)["exigido"], 50)

        # Listagem: o nome do curso é o acesso à turma (sem botão "Turma").
        listagem = self.client.get("/painel/matriculas/")
        self.assertContains(listagem, f'href="/painel/cursos/{curso.pk}/"')
        self.assertNotIn(">Turma</a>", listagem.content.decode())

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
            {"medico": medico.pk, "course": curso.pk},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_formulario_de_matricula_nao_tem_percentual(self):
        """Percentual e prazo são do curso: sem opções nem avisos por matrícula."""
        resposta = self.client.get("/painel/matriculas/nova/")
        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "Percentual exigido")
        self.assertNotContains(resposta, 'name="deadline"')

    def test_formulario_de_curso_renderiza_anexos_com_estilo(self):
        """Anexos com estado atual, botão de escolha, chip do upload e prévia."""
        resposta = self.client.get("/painel/cursos/novo/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Escolher arquivo")
        self.assertContains(resposta, "data-anexo")
        self.assertContains(resposta, "Nenhum arquivo selecionado")
        # Upload novo escolhido ganha chip com X para remover.
        self.assertContains(resposta, "data-anexo-novo")
        self.assertContains(resposta, "data-anexo-cancelar")
        # O texto cru do widget do Django não aparece mais.
        self.assertNotContains(resposta, "Modificar")

    def test_limpar_a_capa_do_curso(self):
        """O checkbox "Limpar" do anexo zera o arquivo (nome -clear padrão)."""
        import io
        import tempfile

        from django.test import override_settings

        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            curso = Course.objects.create(
                title="Curso Capa", status=Course.Status.ACTIVE
            )
            buf = io.BytesIO()
            Image.new("RGB", (8, 8), (10, 120, 100)).save(buf, format="PNG")
            curso.cover.save(
                "capa.png",
                SimpleUploadedFile(
                    "capa.png", buf.getvalue(), content_type="image/png"
                ),
                save=True,
            )

            # O template renderiza o checkbox com o nome que o Django espera.
            pagina = self.client.get(f"/painel/cursos/{curso.pk}/editar/")
            self.assertContains(pagina, 'name="cover-clear"')
            self.assertContains(pagina, "data-anexo-atual")
            # O checkbox virou um "X Limpar" (sr-only + rótulo).
            self.assertContains(pagina, "data-anexo-limpar")
            self.assertContains(pagina, "Limpar")

            resposta = self.client.post(
                f"/painel/cursos/{curso.pk}/editar/",
                {
                    "title": "Curso Capa",
                    "status": "active",
                    "min_percent": "50",
                    "video_min_percent": "",
                    "unlock_threshold": "95",
                    "deadline": "",
                    "cover-clear": "on",
                },
            )
            self.assertRedirects(resposta, f"/painel/cursos/{curso.pk}/")
            curso.refresh_from_db()
            self.assertFalse(curso.cover)

    def test_exportar_planilha_xlsx(self):
        import io as _io

        import openpyxl

        curso = Course.objects.create(title="Curso", status=Course.Status.ACTIVE)
        user = User.objects.create_user("m3", password="x")
        medico = Medico.objects.create(
            user=user, cpf="99988877766", nome="Export", must_change_password=False
        )
        Enrollment.objects.create(medico=medico, course=curso)

        resposta = self.client.get(f"/painel/cursos/{curso.pk}/exportar/")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("spreadsheetml", resposta["Content-Type"])
        self.assertIn(".xlsx", resposta["Content-Disposition"])
        # XLSX é um ZIP no formato OOXML.
        self.assertTrue(resposta.content.startswith(b"PK"))

        wb = openpyxl.load_workbook(_io.BytesIO(resposta.content))
        ws = wb.active
        # Título do curso na linha 1 e cabeçalho na linha 3.
        self.assertIn("Curso", ws["A1"].value)
        cabecalhos = [celula.value for celula in ws[3]]
        self.assertEqual(cabecalhos[0], "Matrícula")
        self.assertIn("Cobertura %", cabecalhos)
        self.assertIn("Aulas abaixo do mínimo", cabecalhos)
        # A matrícula vem nos dados, com a formatação aplicada.
        valores = [celula.value for celula in ws[4]]
        self.assertIn("Export", valores)
        self.assertEqual(ws.freeze_panes, "A4")
        self.assertTrue(ws.auto_filter.ref.startswith("A3"))

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
        # O link de ação "Abrir" (o aria-label do hambúrguer não conta).
        self.assertNotIn(">Abrir</a>", html)

    def test_detalhe_do_curso_nao_tem_voltar_ver_matriculados_nem_abrir(self):
        html = self.client.get(f"/painel/cursos/{self.curso.pk}/").content.decode()
        self.assertNotIn("Voltar", html)
        self.assertNotIn("Ver matriculados", html)
        self.assertNotIn(">Abrir</a>", html)


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
        # A gravação acontece nos pedaços (como o JS faz), com senhas em cada.
        criados, ignorados = processar_lote(self.client, resposta)
        self.assertEqual(len(criados), 2)
        self.assertEqual(ignorados, 0)
        senhas = {item["senha"] for item in criados}
        self.assertEqual(senhas, {"Maria@11122233344", "Joao@55566677788"})
        self.assertEqual(Medico.objects.count(), 2)
        self.assertEqual(User.objects.filter(username__startswith="medico-").count(), 2)

    def test_cpf_duplicado_e_ignorado_sem_cancelar_o_lote(self):
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
        criados, ignorados = processar_lote(self.client, resposta)
        # O duplicado é pulado (contado); o resto do lote é criado.
        self.assertEqual(len(criados), 1)
        self.assertEqual(ignorados, 1)
        self.assertEqual(Medico.objects.count(), 2)

    def test_lote_grande_e_processado_em_varios_pedacos(self):
        linhas = "\n".join(
            f"Aluno {i:02d};10000000{i:03d};aluno{i}@exemplo.com" for i in range(20)
        )
        resposta = self.client.post("/painel/medicos/lote/", {"dados": linhas})
        self.assertEqual(resposta.status_code, 200)

        # Primeiro pedaço: LOTE_TAMANHO (15) dos 20.
        parcial = self.client.post("/painel/medicos/lote/parcial/")
        dados = parcial.json()
        self.assertEqual(dados["total"], 20)
        self.assertEqual(dados["processados"], 15)
        self.assertEqual(len(dados["criados"]), 15)
        self.assertEqual(Medico.objects.count(), 15)

        # Segundo pedaço conclui e limpa a sessão.
        criados, ignorados = processar_lote(self.client, resposta)
        self.assertEqual(len(criados), 5)
        self.assertEqual(Medico.objects.count(), 20)

        # Sessão limpa: um pedaço extra não tem mais o que fazer.
        extra = self.client.post("/painel/medicos/lote/parcial/").json()
        self.assertEqual(extra["total"], 0)

    def test_ui_de_progresso_e_exibida_apos_o_envio(self):
        resposta = self.client.post(
            "/painel/medicos/lote/",
            {"dados": "Ana Paula;11199988877;ana@exemplo.com"},
        )
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()
        self.assertIn('id="lote-barra"', html)
        self.assertIn("Importando alunos", html)
        self.assertIn("var total = 1", html)
        # Os cards da importação acompanham a largura do formulário (max-w-3xl).
        self.assertIn('class="cartao mt-8 max-w-3xl p-6" id="lote-ui"', html)
        processar_lote(self.client, resposta)


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
            medico=self.medicos[0], course=self.curso
        )
        self.client.force_login(self.admin)

    def test_matricular_em_lote_por_lista_de_cpfs(self):
        """A lista de CPFs matricula e o relatório aponta cada falha com o motivo."""
        resposta = self.client.post(
            f"/painel/cursos/{self.curso.pk}/",
            {
                "acao": "matricular_lote_cpfs",
                "cpfs": "\n".join(
                    [
                        self.medicos[0].cpf,  # já matriculado no setUp
                        self.medicos[1].cpf,
                        self.medicos[2].cpf,
                        "12345678901",  # ninguém com este CPF
                        "123",  # inválido
                    ]
                ),
            },
        )
        # assertRedirects consumiria o relatório: captura manual + primeira leitura.
        self.assertEqual(resposta.status_code, 302)
        pagina = self.client.get(resposta.url)
        # médicos[0] já estava matriculado; +2 novos.
        self.assertEqual(self.curso.enrollments.count(), 3)
        for medico in self.medicos[1:]:
            self.assertTrue(
                Enrollment.objects.filter(medico=medico, course=self.curso).exists()
            )

        # Relatório com a contagem de sucesso e cada falha com o motivo.
        self.assertContains(pagina, "Relatório da matrícula em lote")
        self.assertContains(pagina, "2 matriculada(s)")
        self.assertContains(pagina, "Já matriculado neste curso")
        self.assertContains(pagina, "123.456.789-01")
        self.assertContains(pagina, "Não encontrado no cadastro")
        self.assertContains(pagina, "CPF inválido (não tem 11 dígitos)")

        # O relatório é exibido uma única vez (some no reload seguinte).
        segunda = self.client.get(resposta.url)
        self.assertNotContains(segunda, "Relatório da matrícula em lote")

        # O percentual continua sendo o do curso.
        from apps.courses.services import criterios_de_aprovacao

        matricula = Enrollment.objects.get(medico=self.medicos[1], course=self.curso)
        self.assertEqual(criterios_de_aprovacao(matricula)["exigido"], 50)

    def test_matricular_um_cpf_por_vez(self):
        resposta = self.client.post(
            f"/painel/cursos/{self.curso.pk}/",
            {"acao": "matricular_cpf", "cpf": self.medicos[1].cpf},
        )
        self.assertEqual(resposta.status_code, 302)
        pagina = self.client.get(resposta.url)
        self.assertTrue(
            Enrollment.objects.filter(medico=self.medicos[1], course=self.curso).exists()
        )
        self.assertContains(pagina, "matriculado(a) no curso")

        # Segunda vez: avisa que já está, sem duplicar a matrícula.
        self.client.post(
            f"/painel/cursos/{self.curso.pk}/",
            {"acao": "matricular_cpf", "cpf": self.medicos[1].cpf},
        )
        self.assertEqual(self.curso.enrollments.count(), 2)

    def test_cpf_nao_cadastrado_e_apontado(self):
        resposta = self.client.post(
            f"/painel/cursos/{self.curso.pk}/",
            {"acao": "matricular_cpf", "cpf": "55544433322"},
        )
        self.assertEqual(resposta.status_code, 302)
        pagina = self.client.get(resposta.url)
        self.assertContains(pagina, "não está cadastrado")
        self.assertEqual(self.curso.enrollments.count(), 1)

    def test_pagina_do_curso_nao_lista_todos_os_medicos(self):
        user = User.objects.create_user("fora-da-lista", password="x")
        Medico.objects.create(
            user=user,
            cpf="77700011122",
            nome="Zebra Fora da Lista",
            must_change_password=False,
        )
        conteudo = self.client.get(
            f"/painel/cursos/{self.curso.pk}/"
        ).content.decode()
        self.assertIn("Matricular em lote", conteudo)
        self.assertIn("Matricular um aluno", conteudo)
        # Sem lista de checkboxes de todos os médicos cadastrados.
        self.assertNotIn("Zebra Fora da Lista", conteudo)
        self.assertNotIn("data-filtro-lista", conteudo)


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
            medico=self.medico, course=self.curso
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
        criados, _ = processar_lote(self.client, resposta)
        self.assertEqual(len(criados), 2)
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
        criados, _ = processar_lote(self.client, resposta)
        self.assertEqual(len(criados), 2)
        self.assertEqual(Medico.objects.count(), 2)

    def test_aceita_ponto_e_virgula_do_excel(self):
        resposta = self.client.post(
            "/painel/medicos/lote/",
            {"dados": "Maria da Silva; 11122233344; maria@exemplo.com"},
        )
        criados, _ = processar_lote(self.client, resposta)
        self.assertEqual(len(criados), 1)

    def test_aceita_o_padrao_da_lista_de_alunos_com_normalizacao(self):
        """Padrão real do CREMEPE: UTF-8 com BOM, separador ; e caixa alta."""
        conteudo = (
            "\ufeffNOME;CPF;E-MAIL\r\n"
            "MARIA SILVA;111.222.333-44;maria@exemplo.com\r\n"
            "JOAO PAULO DA COSTA;555.666.777-88;joao@exemplo.com\r\n"
        ).encode("utf-8")
        arquivo = SimpleUploadedFile(
            "Lista de alunos.csv", conteudo, content_type="text/csv"
        )
        resposta = self.client.post("/painel/medicos/lote/", {"arquivo": arquivo})
        criados, _ = processar_lote(self.client, resposta)
        self.assertEqual(len(criados), 2)
        # Normalização no banco: caixa alta vira o nome padrão e o CPF limpo.
        self.assertEqual(Medico.objects.get(cpf="11122233344").nome, "Maria Silva")
        self.assertEqual(
            Medico.objects.get(cpf="55566677788").nome, "Joao Paulo da Costa"
        )

    def test_csv_com_cpf_invalido_rejeitado(self):
        arquivo = SimpleUploadedFile(
            "alunos.csv",
            b"Maria da Silva,123,maria@exemplo.com\n",
            content_type="text/csv",
        )
        resposta = self.client.post("/painel/medicos/lote/", {"arquivo": arquivo})
        self.assertContains(resposta, "CPF deve ter 11 dígitos")
        self.assertEqual(Medico.objects.count(), 0)

    def test_reenvio_ignora_cpfs_ja_cadastrados(self):
        resposta = self.client.post(
            "/painel/medicos/lote/",
            {"dados": "Maria da Silva,11122233344"},
        )
        criados, _ = processar_lote(self.client, resposta)
        self.assertEqual(len(criados), 1)

        # Reenvio do mesmo dado: não aborta nem duplica — pula o existente.
        resposta2 = self.client.post(
            "/painel/medicos/lote/",
            {"dados": "Maria da Silva,11122233344"},
        )
        self.assertEqual(resposta2.status_code, 200)
        criados2, ignorados2 = processar_lote(self.client, resposta2)
        self.assertEqual(len(criados2), 0)
        self.assertEqual(ignorados2, 1)
        self.assertEqual(Medico.objects.count(), 1)
        # O pulo do duplicado também vira registro de auditoria.
        duplicado = AuditLog.objects.filter(action="medico_duplicado_ignorado")
        self.assertEqual(duplicado.count(), 1)
        self.assertEqual(duplicado.first().detail.get("cpf"), "11122233344")


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


class TestMinimoPorAulaNoPainel(TestCase):
    """Configuração do percentual mínimo por aula (curso e aula)."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin-min", "a@b.c", "senha12345")
        self.client.force_login(self.admin)

    def _post_curso(self, extra=None):
        dados = {
            "title": "Curso mínimo por aula",
            "description": "",
            "status": "active",
            "min_percent": "50",
            "sequential": "on",
            "unlock_threshold": "95",
        }
        dados.update(extra or {})
        self.client.post("/painel/cursos/novo/", dados)
        return Course.objects.get(title="Curso mínimo por aula")

    def test_form_de_curso_salva_o_minimo_por_aula(self):
        curso = self._post_curso({"video_min_percent": "95"})
        self.assertEqual(curso.video_min_percent, 95)

    def test_form_de_curso_aceita_vazio_como_zerado(self):
        # Comportamento anterior preservado: sem o campo, sem exigência.
        curso = self._post_curso()
        self.assertEqual(curso.video_min_percent, 0)

    def test_form_de_aula_salva_override_e_vazio_usa_curso(self):
        curso = self._post_curso({"video_min_percent": "95"})

        # Aula com valor próprio.
        self.client.post(
            "/painel/videos/novo/",
            {
                "course": curso.pk,
                "order": 1,
                "title": "Aula com override",
                "min_percent": "80",
                "is_required": "on",
            },
        )
        # Aula sem valor: fica em branco e herda o do curso.
        self.client.post(
            "/painel/videos/novo/",
            {
                "course": curso.pk,
                "order": 2,
                "title": "Aula padrão",
                "is_required": "on",
            },
        )
        self.assertEqual(
            Video.objects.get(title="Aula com override").min_percent, 80
        )
        self.assertIsNone(Video.objects.get(title="Aula padrão").min_percent)

    def test_pagina_de_curso_mostra_o_campo_de_minimo_por_aula(self):
        html = self.client.get("/painel/cursos/novo/").content.decode()
        self.assertIn("video_min_percent", html)
        self.assertIn("Percentual mínimo por aula", html)

    def test_planilha_da_turma_explica_aulas_abaixo_do_minimo(self):
        import io as _io

        import openpyxl

        curso = self._post_curso({"video_min_percent": "95"})
        aula1 = Video.objects.create(
            course=curso, order=1, title="Aula 1", duration=500,
            status=Video.Status.READY,
        )
        aula2 = Video.objects.create(
            course=curso, order=2, title="Aula 2", duration=500,
            status=Video.Status.READY,
        )
        medico_user = User.objects.create_user("aluno-min", password="senha12345")
        medico = Medico.objects.create(
            user=medico_user, cpf="33322211100", nome="Aluno Minimo",
            must_change_password=False,
        )
        enrollment = Enrollment.objects.create(
            medico=medico, course=curso
        )
        # Aula 1 inteira + aula 2 a 90%: cobertura total ≥ 50%, mas aula 2
        # abaixo dos 95% exigidos individualmente.
        Progress.objects.create(
            enrollment=enrollment, video=aula1,
            intervals=[[0.0, 500.0]], covered=500.0, furthest=500.0,
            last_position=500.0,
        )
        Progress.objects.create(
            enrollment=enrollment, video=aula2,
            intervals=[[0.0, 450.0]], covered=450.0, furthest=450.0,
            last_position=450.0,
        )

        resposta = self.client.get(f"/painel/cursos/{curso.pk}/exportar/")
        wb = openpyxl.load_workbook(_io.BytesIO(resposta.content))
        ws = wb.active
        planilha = "\n".join(
            " | ".join(str(celula.value or "") for celula in linha)
            for linha in ws.iter_rows()
        )

        self.assertIn("Aulas abaixo do mínimo", planilha)
        self.assertIn("Não", planilha)
        # A pendência cita a aula, a cobertura atual e o mínimo exigido.
        self.assertIn("Aula 2: 90.0% (min. 95%)", planilha)



class TestPaginacaoDasListagens(TestCase):
    """Listagens do painel paginam (alunos: 10 por página; demais: 50)."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin-pag", "a@b.c", "senha12345")
        self.client.force_login(self.admin)

    def _criar_medicos(self, quantidade):
        for i in range(quantidade):
            user = User.objects.create_user(f"pag-med-{i}", password="x")
            Medico.objects.create(
                user=user,
                cpf=f"{i:011d}",
                nome=f"Médico Pag {i:02d}",
                must_change_password=False,
            )

    def test_medicos_paginam_em_blocos_de_10(self):
        self._criar_medicos(60)
        pagina1 = self.client.get("/painel/medicos/").content.decode()
        self.assertIn("<strong>1–10</strong> de 60", pagina1)
        self.assertIn("pagina=2", pagina1)
        self.assertNotIn("Médico Pag 10", pagina1)

        pagina2 = self.client.get("/painel/medicos/?pagina=2").content.decode()
        self.assertIn("<strong>11–20</strong> de 60", pagina2)
        self.assertIn("Médico Pag 11", pagina2)
        self.assertNotIn("Médico Pag 20", pagina2)

    def test_busca_preservada_nos_links_de_pagina(self):
        self._criar_medicos(60)
        html = self.client.get("/painel/medicos/?q=Médico Pag").content.decode()
        self.assertIn("<strong>1–10</strong> de 60", html)
        # O filtro q acompanha os links das páginas.
        self.assertIn("q=M%C3%A9dico", html)

    def test_pagina_invalida_e_absorvida(self):
        self._criar_medicos(60)
        html = self.client.get("/painel/medicos/?pagina=999").content.decode()
        # get_page absorve valores inválidos: cai na última página.
        self.assertIn("<strong>51–60</strong> de 60", html)

    def test_cursos_e_auditoria_tambem_paginam(self):
        for i in range(55):
            Course.objects.create(
                title=f"Curso Pag {i:02d}", status=Course.Status.DRAFT
            )
        html = self.client.get("/painel/cursos/").content.decode()
        self.assertIn("<strong>1–50</strong> de 55", html)
        self.assertIn("pagina=2", html)

        for i in range(55):
            AuditLog.objects.create(action="teste_pag", target=f"log {i}")
        html_aud = self.client.get("/painel/auditoria/").content.decode()
        self.assertIn("<strong>1–50</strong> de 55", html_aud)


class TestListagemDeAlunos(TestCase):
    """Colunas da lista de alunos, filtro de situação e diálogo de detalhes."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin-alunos", "a@b.c", "senha12345"
        )
        self.client.force_login(self.admin)

    def _criar(self, i, ativo=True, provisoria=False):
        user = User.objects.create_user(f"aluno-lista-{i}", password="x")
        return Medico.objects.create(
            user=user,
            cpf=f"{i:011d}",
            nome=f"Aluno Lista {i:02d}",
            ativo=ativo,
            must_change_password=provisoria,
        )

    def test_colunas_sem_matriculas_e_com_acoes(self):
        self._criar(1)
        html = self.client.get("/painel/medicos/").content.decode()
        self.assertNotIn('<th class="cabecalho-tabela">Matrículas</th>', html)
        # Ações alinhado como os demais cabeçalhos (à esquerda), sobre o
        # conteúdo da própria coluna (também à esquerda).
        self.assertIn('<th class="cabecalho-tabela">Ações</th>', html)
        self.assertNotIn("cabecalho-tabela text-right", html)
        # Cinco colunas: Médico, CPF, Última atividade, Situação, Ações.
        self.assertEqual(html.count("cabecalho-tabela"), 5)

    def test_desativar_pedee_confirmacao(self):
        ativo = self._criar(1)
        self._criar(2, ativo=False)
        html = self.client.get("/painel/medicos/").content.decode()
        # Só a desativação pede confirmação (a reativação não).
        self.assertEqual(html.count("data-confirm="), 1)
        self.assertIn("Desativar o aluno", html)
        self.assertIn(f"/painel/medicos/{ativo.pk}/acao/", html)

    def test_ativar_e_desativar_sao_auditados(self):
        medico = self._criar(1)
        self.client.post(
            f"/painel/medicos/{medico.pk}/acao/", {"acao": "desativar"}
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="medico_desativado", target="Aluno Lista 01"
            ).exists()
        )
        self.client.post(f"/painel/medicos/{medico.pk}/acao/", {"acao": "ativar"})
        self.assertTrue(AuditLog.objects.filter(action="medico_ativado").exists())

    def test_dez_alunos_por_pagina(self):
        for i in range(12):
            self._criar(i)
        pagina1 = self.client.get("/painel/medicos/").content.decode()
        self.assertIn("<strong>1–10</strong> de 12", pagina1)
        self.assertNotIn("Aluno Lista 10", pagina1)

        pagina2 = self.client.get("/painel/medicos/?pagina=2").content.decode()
        self.assertIn("<strong>11–12</strong> de 12", pagina2)
        self.assertIn("Aluno Lista 10", pagina2)

    def test_filtro_de_situacao(self):
        self._criar(1)  # ativo, senha definida
        self._criar(2, ativo=False)  # inativo
        self._criar(3, provisoria=True)  # ativo com senha provisória

        ativos = self.client.get("/painel/medicos/?situacao=ativo").content.decode()
        self.assertIn("Aluno Lista 01", ativos)
        self.assertIn("Aluno Lista 03", ativos)  # ativo, mesmo com senha provisória
        self.assertNotIn("Aluno Lista 02", ativos)

        inativos = self.client.get("/painel/medicos/?situacao=inativo").content.decode()
        self.assertNotIn("Aluno Lista 01", inativos)
        self.assertIn("Aluno Lista 02", inativos)

        provisoria = self.client.get(
            "/painel/medicos/?situacao=provisoria"
        ).content.decode()
        self.assertIn("Aluno Lista 03", provisoria)
        self.assertNotIn("Aluno Lista 01", provisoria)
        self.assertNotIn("Aluno Lista 02", provisoria)
        # O select mantém a escolha marcada na página filtrada.
        self.assertIn('value="provisoria" selected', provisoria)

    def test_dialogo_mostra_dados_e_matriculas(self):
        medico = self._criar(1)
        curso = Course.objects.create(
            title="Curso do Dialogo", status=Course.Status.DRAFT
        )
        Enrollment.objects.create(medico=medico, course=curso)

        lista = self.client.get("/painel/medicos/").content.decode()
        # O nome da lista é o gatilho do diálogo.
        self.assertIn("data-medico-dialog", lista)
        self.assertIn(f"/painel/medicos/{medico.pk}/detalhe/", lista)

        resposta = self.client.get(f"/painel/medicos/{medico.pk}/detalhe/")
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()
        self.assertIn("Aluno Lista 01", html)
        self.assertIn("00000000001", html)
        self.assertIn("Matrículas (1)", html)
        self.assertIn("Curso do Dialogo", html)
        # Campos removidos do diálogo a pedido.
        self.assertNotIn("Telefone", html)
        self.assertNotIn("Usuário", html)

    def test_dialogo_sem_matriculas_e_protegido(self):
        medico = self._criar(1, provisoria=True)
        html = self.client.get(
            f"/painel/medicos/{medico.pk}/detalhe/"
        ).content.decode()
        self.assertIn("Nenhuma matrícula", html)
        self.assertIn("provisória (troca obrigatória)", html)

        self.client.logout()
        resposta = self.client.get(f"/painel/medicos/{medico.pk}/detalhe/")
        self.assertEqual(resposta.status_code, 302)


class TestFiltrosDaAuditoria(TestCase):
    """Busca, filtros (ação/autor) e ordenação na página de auditoria."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin-audfil", "a@b.c", "senha12345"
        )
        self.outro = User.objects.create_user("outro-audfil", password="x")
        self.client.force_login(self.admin)
        AuditLog.objects.create(
            action="aluno_criado",
            target="Aluno Um",
            actor=self.outro,
            detail={"cpf": "11122233300"},
        )
        AuditLog.objects.create(
            action="senha_resetada", target="Aluno Dois", actor=self.admin
        )
        AuditLog.objects.create(action="aluno_criado", target="Sistema X")

    def test_busca_por_texto_incluindo_o_detalhe(self):
        html = self.client.get("/painel/auditoria/?q=Aluno Dois").content.decode()
        self.assertIn("Aluno Dois", html)
        self.assertNotIn("Aluno Um", html)
        self.assertNotIn("Sistema X", html)
        self.assertIn("Nenhum registro com esses filtros", self.client.get(
            "/painel/auditoria/?q=nao-existe"
        ).content.decode())

        # A busca varre o detalhe (JSON tratado como texto).
        html = self.client.get("/painel/auditoria/?q=11122233300").content.decode()
        self.assertIn("Aluno Um", html)
        self.assertNotIn("Aluno Dois", html)

    def test_filtro_por_acao_e_autor(self):
        html = self.client.get(
            "/painel/auditoria/?acao=senha_resetada"
        ).content.decode()
        self.assertIn("Aluno Dois", html)
        self.assertNotIn("Aluno Um", html)
        self.assertIn('value="senha_resetada" selected', html)

        html = self.client.get("/painel/auditoria/?ator=outro-audfil").content.decode()
        self.assertIn("Aluno Um", html)
        self.assertNotIn("Aluno Dois", html)

        html = self.client.get("/painel/auditoria/?ator=sistema").content.decode()
        self.assertIn("Sistema X", html)
        self.assertNotIn("Aluno Um", html)
        self.assertIn('value="sistema" selected', html)

    def test_ordenacao_recentes_antigas_e_acao(self):
        antigas = self.client.get(
            "/painel/auditoria/?ordenacao=antigas"
        ).content.decode()
        # Cronológica: a ordem de criação.
        self.assertTrue(
            antigas.index("Aluno Um") < antigas.index("Aluno Dois")
            < antigas.index("Sistema X")
        )
        self.assertIn('value="antigas" selected', antigas)

        por_acao = self.client.get(
            "/painel/auditoria/?ordenacao=acao"
        ).content.decode()
        # aluno_criado antes de senha_resetada: "Aluno Dois" fica por último.
        self.assertTrue(por_acao.index("Aluno Dois") > por_acao.index("Aluno Um"))
        self.assertTrue(por_acao.index("Aluno Dois") > por_acao.index("Sistema X"))

    def test_filtros_ativos_mostram_limpar_e_preservam_na_pagina(self):
        html = self.client.get(
            "/painel/auditoria/?q=Aluno&ordenacao=antigas"
        ).content.decode()
        self.assertIn("Limpar", html)
        self.assertIn('value="antigas" selected', html)
        # Os selects continuam marcados e a lista segue paginável.
        self.assertIn('name="acao"', html)
        self.assertIn('name="ator"', html)
