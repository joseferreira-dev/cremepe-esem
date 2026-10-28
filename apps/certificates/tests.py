"""Testes da emissão do certificado (critério, PDF, acesso aluno/painel)."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Medico
from apps.courses.models import AuditLog, Course, Enrollment, Video

from .models import Certificate
from .services import (
    CertificadoIndisponivel,
    caminho_pdf,
    carga_horaria,
    emitir_certificado,
)


def criar_aluno(username="aluno-cert", cpf="11122233344", nome="Maria do Socorro Teste"):
    user = User.objects.create_user(username=username, password="senha12345")
    medico = Medico.objects.create(
        user=user, cpf=cpf, nome=nome, must_change_password=False
    )
    return user, medico


class BaseCertificado(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin-cert", "cert@b.c", "senha12345", is_staff=True
        )
        self.aluno_user, self.medico = criar_aluno()
        self.curso = Course.objects.create(
            title="18º Curso de Atualização em Ética e Bioética",
            status=Course.Status.ACTIVE,
            min_percent=50,
        )
        for ordem in (1, 2, 3):
            Video.objects.create(
                course=self.curso,
                order=ordem,
                title=f"Aula {ordem}",
                duration=15,
                status=Video.Status.READY,
            )
        self.matricula = Enrollment.objects.create(
            medico=self.medico, course=self.curso
        )

    def tearDown(self):
        # Os PDFs gerados vivem fora do banco: remove para não deixar lixo.
        for certificate in Certificate.objects.all():
            caminho = caminho_pdf(certificate.code)
            if caminho.exists():
                caminho.unlink()

    def aprovar(self):
        self.matricula.status = Enrollment.Status.APPROVED
        self.matricula.save(update_fields=["status"])
        return self.matricula


class TestCriterioDeEmissao(BaseCertificado):
    def test_nao_emite_sem_aprovacao(self):
        with self.assertRaises(CertificadoIndisponivel):
            emitir_certificado(self.matricula)
        self.assertEqual(Certificate.objects.count(), 0)

    def test_emite_com_matricula_aprovada(self):
        certificate = emitir_certificado(self.aprovar())
        self.assertEqual(Certificate.objects.count(), 1)
        self.assertTrue(certificate.code.startswith("ESEM-"))
        self.assertEqual(certificate.enrollment, self.matricula)

    def test_pdf_gerado_com_o_fundo(self):
        certificate = emitir_certificado(self.aprovar())
        caminho = caminho_pdf(certificate.code)
        self.assertTrue(caminho.exists())
        conteudo = caminho.read_bytes()
        self.assertTrue(conteudo.startswith(b"%PDF"))
        # O fundo do modelo (~210 KB) precisa estar embutido.
        self.assertGreater(len(conteudo), 50_000)
        self.assertEqual(certificate.pdf_path, str(caminho.relative_to(caminho.parents[1])))

    def test_payload_congela_os_dados_impressos(self):
        certificate = emitir_certificado(self.aprovar())
        self.assertEqual(certificate.payload["aluno"], "Maria do Socorro Teste")
        self.assertEqual(certificate.payload["curso"], self.curso.title)
        self.assertEqual(certificate.payload["carga"], "45 segundos")
        self.assertNotIn("fundo", certificate.payload)

    def test_emissao_repetida_reutiliza_o_mesmo_certificado(self):
        self.aprovar()
        primeiro = emitir_certificado(self.matricula)
        codigo = primeiro.code
        self.matricula.refresh_from_db()
        segundo = emitir_certificado(self.matricula)
        self.assertEqual(primeiro.pk, segundo.pk)
        self.assertEqual(segundo.code, codigo)
        self.assertEqual(Certificate.objects.count(), 1)

    def test_primeira_emissao_e_registrada_na_auditoria(self):
        self.aprovar()
        emitir_certificado(self.matricula, actor=self.admin, ip="127.0.0.1")
        registro = AuditLog.objects.filter(action="certificado_emitido").first()
        self.assertIsNotNone(registro)
        self.assertEqual(registro.actor, self.admin)
        self.assertEqual(registro.detail["aluno"], "Maria do Socorro Teste")
        # Baixa seguinte não repete o registro.
        emitir_certificado(self.matricula, actor=self.admin)
        self.assertEqual(
            AuditLog.objects.filter(action="certificado_emitido").count(), 1
        )


class TestCargaHoraria(TestCase):
    def test_segundos(self):
        self.assertEqual(carga_horaria(45), "45 segundos")

    def test_minutos(self):
        self.assertEqual(carga_horaria(1500), "25 minutos")
        self.assertEqual(carga_horaria(90), "1 minuto")

    def test_horas(self):
        self.assertEqual(carga_horaria(3600), "1 hora")
        self.assertEqual(carga_horaria(5400), "1 hora e 30 minutos")
        self.assertEqual(carga_horaria(7200), "2 horas")

    def test_zerado(self):
        self.assertEqual(carga_horaria(0), "0 segundos")


class TestAcessoAoCertificado(BaseCertificado):
    def url_aluno(self):
        return reverse("certificado_baixar", args=[self.matricula.pk])

    def test_anonimo_vai_para_o_login(self):
        resposta = self.client.get(self.url_aluno())
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/entrar/", resposta.url)

    def test_aluno_baixa_o_proprio_pdf(self):
        self.aprovar()
        self.client.force_login(self.aluno_user)
        resposta = self.client.get(self.url_aluno())
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Content-Type"], "application/pdf")
        self.assertIn("attachment", resposta["Content-Disposition"])
        self.assertIn(
            "certificado-maria-do-socorro-teste.pdf", resposta["Content-Disposition"]
        )
        self.assertTrue(resposta.content.startswith(b"%PDF"))

    def test_aluno_de_outro_nao_baixa(self):
        self.aprovar()
        outro_user, _ = criar_aluno("outro-aluno", "99988877766", "Outro Médico")
        self.client.force_login(outro_user)
        resposta = self.client.get(self.url_aluno())
        self.assertEqual(resposta.status_code, 403)

    def test_staff_do_painel_baixa(self):
        self.aprovar()
        self.client.force_login(self.admin)
        resposta = self.client.get(
            reverse("painel_certificado", args=[self.matricula.pk])
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Content-Type"], "application/pdf")
        self.assertTrue(resposta.content.startswith(b"%PDF"))

    def test_painel_exige_autenticacao(self):
        resposta = self.client.get(
            reverse("painel_certificado", args=[self.matricula.pk])
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/painel/entrar/", resposta.url)

    def test_botao_aparece_somente_quando_aprovada(self):
        url = reverse("course", args=[self.matricula.pk])
        self.client.force_login(self.aluno_user)

        resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "Baixar certificado")

        self.aprovar()
        resposta = self.client.get(url)
        self.assertContains(resposta, "Baixar certificado")
        self.assertContains(resposta, reverse("certificado_baixar", args=[self.matricula.pk]))


class TestSemRegressaoDoCurso(BaseCertificado):
    def test_a_cancelada_nao_emite(self):
        self.matricula.status = Enrollment.Status.CANCELLED
        self.matricula.save(update_fields=["status"])
        with self.assertRaises(CertificadoIndisponivel):
            emitir_certificado(self.matricula)

    def test_reprovada_nao_emite(self):
        self.matricula.status = Enrollment.Status.REJECTED
        self.matricula.save(update_fields=["status"])
        with self.assertRaises(CertificadoIndisponivel):
            emitir_certificado(self.matricula)
