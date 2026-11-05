"""Testes da emissão do certificado (critério, PDF, acesso aluno/painel)."""

from __future__ import annotations

import io
import zipfile

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Medico
from apps.courses.models import AuditLog, Course, Enrollment, Video

from .docx import ModeloInvalido, extrair_modelo, paragrafos_do_modelo
from .models import Certificate
from .services import (
    CertificadoIndisponivel,
    aplicar_modelo_certificado,
    caminho_pdf,
    carga_horaria,
    dados_do_certificado,
    emitir_certificado,
    gerar_codigo,
    limpar_modelo_certificado,
    renderizar_html,
)
from .views import TENTATIVAS_MAX_POR_SESSAO

# Arte sintética (PNG válido) para os DOCX de teste — o projeto não tem
# mais assets de modelo embutido (todo curso envia o próprio .docx).
def _arte_sintetica() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (320, 226), (15, 118, 110)).save(buffer, format="PNG")
    return buffer.getvalue()

CONTEUDO_DOCX = [
    "Declaramos para os devidos fins que",
    "NOME COMPLETO",
    "participou do Curso de Teste, realizado pela ESEM, em 2026.",
]


def docx_de_teste(paragrafos=None, com_imagem=True) -> bytes:
    """Monta um .docx mínimo (ZIP com document.xml + arte)."""
    paragrafos = paragrafos if paragrafos is not None else CONTEUDO_DOCX
    corpo = "".join(
        f"<w:p><w:r><w:t>{texto}</w:t></w:r></w:p>" for texto in paragrafos
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pacote:
        pacote.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main">'
            f"<w:body>{corpo}</w:body></w:document>",
        )
        if com_imagem:
            pacote.writestr("word/media/image1.png", _arte_sintetica())
    return buffer.getvalue()


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
        # Todo curso precisa de modelo para emitir: o base já tem um válido.
        aplicar_modelo_certificado(self.curso, docx_de_teste())
        self.curso.save()

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

    def test_pdf_e_gerado_com_o_modelo_do_curso(self):
        certificate = emitir_certificado(self.aprovar())
        caminho = caminho_pdf(certificate.code)
        self.assertTrue(caminho.exists())
        conteudo = caminho.read_bytes()
        self.assertTrue(conteudo.startswith(b"%PDF"))
        self.assertGreater(len(conteudo), 10_000)
        self.assertEqual(certificate.pdf_path, str(caminho.relative_to(caminho.parents[1])))

    def test_emissao_sem_modelo_no_curso_e_bloqueada(self):
        """Sem modelo cadastrado não existe certificado (não há modelo padrão)."""
        curso_sem = Course.objects.create(
            title="Curso sem modelo", status=Course.Status.ACTIVE, min_percent=50
        )
        matricula = Enrollment.objects.create(
            medico=self.medico, course=curso_sem
        )
        matricula.status = Enrollment.Status.APPROVED
        matricula.save(update_fields=["status"])

        with self.assertRaises(CertificadoIndisponivel):
            emitir_certificado(matricula)
        self.assertEqual(Certificate.objects.count(), 0)

    def test_download_sem_modelo_mostra_mensagem_e_nao_emite(self):
        curso_sem = Course.objects.create(
            title="Curso sem modelo", status=Course.Status.ACTIVE, min_percent=50
        )
        matricula = Enrollment.objects.create(
            medico=self.medico, course=curso_sem
        )
        matricula.status = Enrollment.Status.APPROVED
        matricula.save(update_fields=["status"])

        self.client.force_login(self.aluno_user)
        resposta = self.client.get(
            reverse("certificado_baixar", args=[matricula.pk])
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Certificate.objects.count(), 0)
        # A mensagem de erro fica visível na página do curso do aluno.
        pagina = self.client.get(resposta.url)
        self.assertContains(pagina, "modelo de certificado")

    def test_codigo_e_unico_com_48_bits_de_aleatoriedade(self):
        """Formato ESEM-<ano>-<12 hex>: legível e com colisão praticamente nula."""
        codigos = {gerar_codigo() for _ in range(20)}
        self.assertEqual(len(codigos), 20)
        for codigo in codigos:
            self.assertTrue(codigo.startswith(f"ESEM-{timezone.localdate().year}-"))
            parte = codigo.rsplit("-", 1)[1]
            self.assertEqual(len(parte), 12)
            int(parte, 16)  # é hexadecimal válido

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
        # Sem página de erro: volta aos cursos com aviso.
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/cursos/", resposta.url)
        pagina = self.client.get(resposta.url)
        self.assertContains(pagina, "não é seu")

    def test_staff_do_painel_baixa(self):
        self.aprovar()
        self.client.force_login(self.admin)
        resposta = self.client.get(
            reverse("painel_certificado", args=[self.matricula.pk])
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Content-Type"], "application/pdf")
        self.assertTrue(resposta.content.startswith(b"%PDF"))

    def test_staff_nao_baixa_pela_rota_do_aluno(self):
        """A rota do aluno é do titular; administrador volta para o painel."""
        self.aprovar()
        self.client.force_login(self.admin)
        resposta = self.client.get(self.url_aluno())
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/painel/", resposta.url)

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
    def test_reprovada_nao_emite(self):
        self.matricula.status = Enrollment.Status.REJECTED
        self.matricula.save(update_fields=["status"])
        with self.assertRaises(CertificadoIndisponivel):
            emitir_certificado(self.matricula)


class TestPaginaDeCertificados(BaseCertificado):
    """A página "Meus certificados" do menu do aluno."""

    def url(self):
        return reverse("meus_certificados")

    def test_anonimo_vai_para_o_login(self):
        resposta = self.client.get(self.url())
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/entrar/", resposta.url)

    def test_staff_nao_acessa_a_pagina(self):
        """Área do aluno: administrador volta para o painel, sem erro."""
        self.client.force_login(self.admin)
        resposta = self.client.get(self.url())
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/painel/", resposta.url)

    def test_lista_o_certificado_emitido_com_codigo_e_link(self):
        self.aprovar()
        certificate = emitir_certificado(self.matricula)
        self.client.force_login(self.aluno_user)

        resposta = self.client.get(self.url())
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, certificate.code)
        self.assertContains(resposta, self.curso.title)
        self.assertContains(
            resposta, reverse("certificado_baixar", args=[self.matricula.pk])
        )
        self.assertContains(resposta, "Certificados obtidos")

    def test_textos_da_pagina_sao_simplificados(self):
        """Estado vazio com o texto novo e sem o trecho do rodapé do PDF."""
        self.client.force_login(self.aluno_user)
        conteudo = self.client.get(self.url()).content.decode()
        self.assertIn(
            "Os certificados são liberados quando os cursos são concluídos",
            conteudo,
        )
        self.assertNotIn("Seu certificado é liberado", conteudo)
        self.assertNotIn("rodapé do PDF", conteudo)

    def test_aprovada_sem_emissao_aparece_como_pronta(self):
        self.aprovar()
        self.client.force_login(self.aluno_user)

        resposta = self.client.get(self.url())
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Prontos para baixar")
        self.assertContains(
            resposta, reverse("certificado_baixar", args=[self.matricula.pk])
        )
        # A página só sinaliza: a emissão acontece no momento da baixa.
        self.assertEqual(Certificate.objects.count(), 0)

    def test_nao_mostra_certificado_de_outro_aluno(self):
        self.aprovar()
        certificate = emitir_certificado(self.matricula)
        outro_user, _ = criar_aluno("outro-lista", "55566677788", "Outro Médico")
        self.client.force_login(outro_user)

        resposta = self.client.get(self.url())
        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, certificate.code)
        self.assertNotContains(resposta, self.curso.title)
        self.assertContains(resposta, "Nenhum certificado ainda")

    def test_matricula_em_andamento_nao_aparece(self):
        self.client.force_login(self.aluno_user)
        resposta = self.client.get(self.url())
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Nenhum certificado ainda")
        self.assertNotContains(resposta, self.curso.title)

    def test_menu_do_aluno_mostra_o_link(self):
        self.client.force_login(self.aluno_user)
        resposta = self.client.get(reverse("my_courses"))
        self.assertContains(resposta, reverse("meus_certificados"))


class TestValidacaoPublica(BaseCertificado):
    """Validação pública de certificados: qualquer pessoa, sem login."""

    def url(self, codigo=""):
        base = reverse("validar_certificado")
        return f"{base}?codigo={codigo}" if codigo else base

    def test_pagina_publica_abre_sem_login(self):
        resposta = self.client.get(self.url())
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Validação de certificados")
        self.assertContains(resposta, "Código de autenticação")

    def test_codigo_valido_mostra_os_dados_impressos(self):
        self.aprovar()
        certificate = emitir_certificado(self.matricula)

        resposta = self.client.get(self.url(certificate.code))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Certificado válido")
        self.assertContains(resposta, "Maria do Socorro Teste")
        self.assertContains(resposta, self.curso.title)
        self.assertContains(resposta, certificate.code)

    def test_codigo_em_minusculo_e_com_espacos_funciona(self):
        self.aprovar()
        certificate = emitir_certificado(self.matricula)
        resposta = self.client.get(
            reverse("validar_certificado") + "?codigo=%20" + certificate.code.lower() + "%20"
        )
        self.assertContains(resposta, "Certificado válido")

    def test_codigo_inexistente_nao_vaza_dados(self):
        self.aprovar()
        certificate = emitir_certificado(self.matricula)

        resposta = self.client.get(self.url("ESEM-2026-00000000"))
        self.assertContains(resposta, "Código não encontrado")
        self.assertNotContains(resposta, "Maria do Socorro Teste")
        self.assertNotContains(resposta, certificate.code)

    def test_nunca_expoem_cpf(self):
        self.aprovar()
        certificate = emitir_certificado(self.matricula)
        resposta = self.client.get(self.url(certificate.code))
        # O CPF (dado cadastral sigiloso) nunca pode aparecer na consulta.
        self.assertNotContains(resposta, self.medico.cpf)

    def test_acerto_e_registrado_na_auditoria(self):
        self.aprovar()
        certificate = emitir_certificado(self.matricula)

        self.client.get(self.url(certificate.code))
        registro = AuditLog.objects.filter(action="certificado_validado").first()
        self.assertIsNotNone(registro)
        self.assertEqual(registro.target, certificate.code)

    def test_bloqueia_apos_tentativas_falhas(self):
        self.aprovar()
        certificate = emitir_certificado(self.matricula)

        for _ in range(TENTATIVAS_MAX_POR_SESSAO):
            resposta = self.client.get(self.url("ESEM-2026-FFFFFFFF"))
            self.assertNotContains(resposta, "Certificado válido")

        # Estourou o limite: nem o código certo passa durante o bloqueio.
        resposta = self.client.get(self.url(certificate.code))
        self.assertContains(resposta, "Muitas tentativas")
        self.assertNotContains(resposta, "Certificado válido")

    @override_settings(SITE_URL="https://cursos.example.org")
    def test_pdf_imprime_o_link_de_validacao(self):
        dados = dados_do_certificado(self.matricula, "ESEM-2026-ABCDEF12")
        self.assertEqual(
            dados["validacao"],
            "https://cursos.example.org/validar/?codigo=ESEM-2026-ABCDEF12",
        )

    @override_settings(SITE_URL="")
    def test_sem_site_url_o_link_do_pdf_fica_vazio(self):
        dados = dados_do_certificado(self.matricula, "ESEM-2026-ABCDEF12")
        self.assertEqual(dados["validacao"], "")

    def test_menu_mostra_o_link_de_validacao_para_visitantes(self):
        resposta = self.client.get(self.url())
        self.assertContains(resposta, reverse("validar_certificado"))


class TestModeloPorCurso(BaseCertificado):
    """Modelo de certificado enviado pelo curso (DOCX) + extração + verso."""

    def _upload(self, paragrafos=None, com_imagem=True):
        return SimpleUploadedFile(
            "modelo.docx",
            docx_de_teste(paragrafos, com_imagem),
            content_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
        )

    def _post_curso_com_modelo(self, arquivo):
        self.client.force_login(self.admin)
        return self.client.post(
            "/painel/cursos/novo/",
            {
                "title": "Curso com modelo",
                "description": "",
                "status": Course.Status.ACTIVE,
                "min_percent": "50",
                "sequential": "on",
                "unlock_threshold": "95",
                "certificate_model": arquivo,
            },
        )

    # ------------------------------------------------------------------
    # Extração no envio (painel)
    # ------------------------------------------------------------------
    def test_envio_pelo_painel_extrai_fundo_e_texto(self):
        resposta = self._post_curso_com_modelo(self._upload())
        self.assertEqual(resposta.status_code, 302)

        curso = Course.objects.get(title="Curso com modelo")
        self.assertTrue(curso.certificate_background)
        self.assertIn("NOME COMPLETO", curso.certificate_text)
        self.assertIn("Curso de Teste", curso.certificate_text)
        self.assertTrue(curso.certificate_model)

    def test_docx_sem_campo_de_nome_e_recusado(self):
        resposta = self._post_curso_com_modelo(
            self._upload(paragrafos=["Texto sem o campo", "Outro parágrafo"])
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "precisa conter o campo NOME COMPLETO")
        self.assertFalse(Course.objects.filter(title="Curso com modelo").exists())

    def test_docx_sem_imagem_e_recusado(self):
        resposta = self._post_curso_com_modelo(self._upload(com_imagem=False))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "não contém imagem de fundo")
        self.assertFalse(Course.objects.filter(title="Curso com modelo").exists())

    def test_arquivo_quebrado_e_recusado(self):
        arquivo = SimpleUploadedFile("modelo.docx", b"nao-e-um-zip")
        resposta = self._post_curso_com_modelo(arquivo)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "não é um DOCX válido")

    def test_limpar_o_modelo_bloqueia_a_emissao(self):
        limpar_modelo_certificado(self.curso)
        self.curso.save()

        self.curso.refresh_from_db()
        self.assertFalse(self.curso.certificate_model)
        self.assertFalse(self.curso.certificate_background)
        self.assertEqual(self.curso.certificate_text, "")

        # Sem modelo não há certificado: a emissão é recusada.
        self.aprovar()
        with self.assertRaises(CertificadoIndisponivel):
            emitir_certificado(self.matricula)
        self.assertEqual(Certificate.objects.count(), 0)

    # ------------------------------------------------------------------
    # Unidade da extração
    # ------------------------------------------------------------------
    def test_extracao_levanta_modelo_invalido_sem_placeholders(self):
        with self.assertRaises(ModeloInvalido):
            extrair_modelo(docx_de_teste(paragrafos=["apenas texto"]))

    def test_paragrafos_substituem_o_nome_do_aluno(self):
        paragrafos = paragrafos_do_modelo("\n\n".join(CONTEUDO_DOCX), "Maria")
        self.assertEqual(len(paragrafos), 3)
        self.assertEqual(paragrafos[1]["texto"], "Maria")
        self.assertTrue(paragrafos[1]["nome"])
        self.assertFalse(paragrafos[0]["nome"])

    # ------------------------------------------------------------------
    # Emissão
    # ------------------------------------------------------------------
    def test_emissao_usa_o_modelo_do_curso_com_o_nome_substituido(self):
        aplicar_modelo_certificado(self.curso, docx_de_teste())
        self.curso.save()
        self.aprovar()

        certificate = emitir_certificado(self.matricula)
        html = renderizar_html(certificate)

        self.assertNotIn("NOME COMPLETO", html)
        self.assertIn("Maria do Socorro Teste", html)
        self.assertIn("participou do Curso de Teste", html)
        # Arte extraída do DOCX como fundo do anverso.
        self.assertIn("data:image/png;base64", html)
        self.assertIn('class="fundo"', html)

    @override_settings(SITE_URL="https://cursos.example.org")
    def test_pdf_do_modelo_do_curso_e_gerado(self):
        aplicar_modelo_certificado(self.curso, docx_de_teste())
        self.curso.save()
        self.aprovar()

        certificate = emitir_certificado(self.matricula)
        caminho = caminho_pdf(certificate.code)
        self.assertTrue(caminho.exists())
        conteudo = caminho.read_bytes()
        self.assertTrue(conteudo.startswith(b"%PDF"))
        self.assertGreater(len(conteudo), 10_000)

    def test_extração_lazy_quando_so_o_docx_foi_salvo(self):
        # Caminho do admin: arquivo salvo sem a extração do formulário.
        self.curso.certificate_model.save(
            "modelo.docx", ContentFile(docx_de_teste()), save=True
        )
        self.aprovar()
        certificate = emitir_certificado(self.matricula)

        html = renderizar_html(certificate)
        self.assertIn("Maria do Socorro Teste", html)
        self.curso.refresh_from_db()
        self.assertTrue(self.curso.certificate_background)

    # ------------------------------------------------------------------
    # Verso de validação (comum a todos os modelos)
    # ------------------------------------------------------------------
    @override_settings(SITE_URL="https://cursos.example.org")
    def test_verso_com_dados_e_qr_no_modelo_do_curso(self):
        aplicar_modelo_certificado(self.curso, docx_de_teste())
        self.curso.save()
        self.aprovar()

        certificate = emitir_certificado(self.matricula)
        html = renderizar_html(certificate)

        self.assertIn("Validação do certificado", html)
        self.assertIn("Escaneie para validar", html)
        self.assertIn("https://cursos.example.org/validar/?codigo=", html)
        self.assertIn(certificate.code, html)
        # QR em PNG embutido como data URI (e fora do payload auditado).
        self.assertIn("data:image/png;base64,", html)
        self.assertNotIn("qr", certificate.payload)

    @override_settings(SITE_URL="")
    def test_sem_site_url_o_verso_fica_sem_qr(self):
        self.aprovar()
        certificate = emitir_certificado(self.matricula)
        html = renderizar_html(certificate)

        # O verso continua (validação pelo código), só sem QR/URL.
        self.assertIn("Validação do certificado", html)
        self.assertNotIn("Escaneie para validar", html)
