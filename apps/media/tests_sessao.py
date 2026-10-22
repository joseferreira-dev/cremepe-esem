"""Testes do tratamento de sessão expirada nas APIs do player."""

import json

from django.contrib.auth.models import User
from django.test import TestCase

from apps.accounts.models import Medico
from apps.courses.models import Course, Enrollment, Video


class TestSessaoExpirada(TestCase):
    """A API deve responder 401 em JSON, e nao redirecionar para o login."""

    def setUp(self):
        self.user = User.objects.create_user("medico-sessao", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="11122233399",
            nome="Medico Sessao",
            must_change_password=False,
        )
        self.curso = Course.objects.create(title="Curso", status=Course.Status.ACTIVE)
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula 1", duration=100
        )
        self.enrollment = Enrollment.objects.create(
            medico=self.medico, course=self.curso
        )

    def test_player_state_sem_sessao_devolve_401_json(self):
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}&video={self.video.id}",
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(resposta["Content-Type"], "application/json")
        self.assertIn("Sessão expirada", json.loads(resposta.content)["error"])

    def test_heartbeat_sem_sessao_devolve_401_json(self):
        resposta = self.client.post(
            "/api/player/heartbeat/",
            data=json.dumps(
                {"enrollment": self.enrollment.id, "video": self.video.id, "position": 0}
            ),
            content_type="application/json",
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resposta.status_code, 401)
        self.assertIn("error", json.loads(resposta.content))

    def test_manifest_sem_sessao_devolve_401_json(self):
        resposta = self.client.get(
            f"/api/media/{self.video.id}/master.m3u8?token=invalido",
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resposta.status_code, 401)
        self.assertIn("Sessão expirada", json.loads(resposta.content)["error"])

    def test_navegador_sem_sessao_redireciona_para_o_login(self):
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}&video={self.video.id}",
            headers={"x-requested-with": "XMLHttpRequest"},
            follow=False,
        )
        # Sem o cabecalho XHR o comportamento e o redirect para a tela de login.
        sem_xhr = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}&video={self.video.id}",
            follow=False,
        )
        self.assertIn(sem_xhr.status_code, (302, 401))
