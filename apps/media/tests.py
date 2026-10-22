"""Testes de upload em blocos, assinatura de URLs e entrega do vídeo."""

import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Medico
from apps.courses.models import Course, Enrollment, Progress, Video
from apps.media.services import (
    build_signed_variant_playlist,
    parse_playlist,
    sign_payload,
    verify_token,
)


class TestAssinatura(TestCase):
    def test_token_valido(self):
        token = sign_payload({"v": 1, "e": 2, "exp": 9999999999})
        payload = verify_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["v"], 1)

    def test_token_adulterado(self):
        token = sign_payload({"v": 1, "exp": 9999999999})
        partes = token.split(".")
        partes[0] = partes[0][:-2] + "AA"
        self.assertIsNone(verify_token(".".join(partes)))

    def test_token_expirado(self):
        token = sign_payload({"v": 1, "exp": timezone.now().timestamp() - 10})
        self.assertIsNone(verify_token(token))

    def test_token_inexistente(self):
        self.assertIsNone(verify_token("lixo"))
        self.assertIsNone(verify_token(""))


class TestPlaylist(TestCase):
    def test_parse_playlist(self):
        texto = (
            "#EXTM3U\n"
            "#EXT-X-TARGETDURATION:4\n"
            "#EXTINF:4.000,\n"
            "seg_00000.ts\n"
            "#EXTINF:4.000,\n"
            "seg_00001.ts\n"
        )
        segmentos = parse_playlist(texto)
        self.assertEqual(len(segmentos), 2)
        self.assertEqual(segmentos[0][1], 0)
        self.assertAlmostEqual(segmentos[1][1], 4.0)

    def test_tokens_dos_segmentos_cobrem_a_duracao_do_video(self):
        texto = (
            "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
            "#EXTINF:14400.0,\nseg_00000.ts\n#EXT-X-ENDLIST\n"
        )
        corpo = build_signed_variant_playlist(1, 2, "720p", texto)
        linha = next(l for l in corpo.splitlines() if l.startswith("/api/media/"))
        payload = verify_token(linha.split("token=")[1])
        self.assertIsNotNone(payload)
        # Video de 4 h: o token nao pode expirar antes do fim da reproducao.
        self.assertGreater(
            payload["exp"], timezone.now().timestamp() + 4 * 3600
        )


class TestUpload(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@b.c", "senha12345")
        self.medico_user = User.objects.create_user("medico", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.medico_user,
            cpf="12345678901",
            nome="Medico",
            must_change_password=False,
        )
        self.curso = Course.objects.create(title="Curso", status=Course.Status.ACTIVE)
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula 1", duration=100
        )
        self.enrollment = Enrollment.objects.create(
            medico=self.medico, course=self.curso
        )

    def test_upload_completo_em_blocos(self):
        self.client.force_login(self.admin)
        conteudo = b"x" * 1000

        resposta = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(
                {"video_id": self.video.id, "filename": "aula.mp4", "size": len(conteudo)}
            ),
            content_type="application/json",
        )
        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        upload_id = dados["upload_id"]
        self.assertEqual(dados["total_chunks"], 1)

        resposta = self.client.post(
            f"/api/uploads/{upload_id}/chunk/",
            data=conteudo,
            content_type="application/octet-stream",
            headers={"X-Chunk-Index": "0"},
        )
        self.assertEqual(resposta.status_code, 200)

        resposta = self.client.post(f"/api/uploads/{upload_id}/complete/")
        self.assertEqual(resposta.status_code, 200)

        self.video.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.QUEUED)
        destino = Path(settings.MEDIA_ROOT) / "orig" / f"{self.video.id}.mp4"
        self.assertTrue(destino.exists())
        self.assertEqual(destino.stat().st_size, len(conteudo))

        destino.unlink(missing_ok=True)

    def test_bloco_com_tamanho_errado(self):
        self.client.force_login(self.admin)
        resposta = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(
                {"video_id": self.video.id, "filename": "aula.mp4", "size": 100}
            ),
            content_type="application/json",
        )
        upload_id = resposta.json()["upload_id"]
        resposta = self.client.post(
            f"/api/uploads/{upload_id}/chunk/",
            data=b"curto",
            content_type="application/octet-stream",
            headers={"X-Chunk-Index": "0"},
        )
        self.assertEqual(resposta.status_code, 400)

    def test_aluno_nao_pode_enviar(self):
        self.client.force_login(self.medico_user)
        resposta = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(
                {"video_id": self.video.id, "filename": "aula.mp4", "size": 100}
            ),
            content_type="application/json",
        )
        self.assertIn(resposta.status_code, (302, 403))

    def test_concluir_sem_todos_os_blocos(self):
        self.client.force_login(self.admin)
        resposta = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(
                {"video_id": self.video.id, "filename": "aula.mp4", "size": 100}
            ),
            content_type="application/json",
        )
        upload_id = resposta.json()["upload_id"]
        resposta = self.client.post(f"/api/uploads/{upload_id}/complete/")
        self.assertEqual(resposta.status_code, 400)

    def test_sessao_e_retomada_apos_queda(self):
        self.client.force_login(self.admin)
        payload = {
            "video_id": self.video.id,
            "filename": "aula.mp4",
            "size": 1000,
        }
        primeira = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(payload),
            content_type="application/json",
        ).json()

        # Simula queda no meio: grava o primeiro bloco e reabre a sessão.
        self.client.post(
            f"/api/uploads/{primeira['upload_id']}/chunk/",
            data=b"x" * 1000,
            content_type="application/octet-stream",
            headers={"X-Chunk-Index": "0"},
        )

        segunda = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(payload),
            content_type="application/json",
        ).json()

        self.assertEqual(segunda["upload_id"], primeira["upload_id"])
        self.assertTrue(segunda.get("retomado"))
        self.assertEqual(segunda["received"], [0])


class TestPlaylistAssinada(TestCase):
    """A playlist precisa manter as tags HLS, senão o hls.js não reproduz."""

    def setUp(self):
        self.medico_user = User.objects.create_user("medico5", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.medico_user,
            cpf="44455566677",
            nome="Medico 4",
            must_change_password=False,
        )
        self.curso = Course.objects.create(title="Curso", status=Course.Status.ACTIVE)
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula 1", duration=100
        )
        self.enrollment = Enrollment.objects.create(
            medico=self.medico, course=self.curso
        )

        # Grava uma playlist HLS de verdade no disco
        pasta = Path(settings.MEDIA_ROOT) / "hls" / str(self.video.pk) / "720p"
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "index.m3u8").write_text(
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-TARGETDURATION:4\n"
            "#EXT-X-MEDIA-SEQUENCE:0\n"
            "#EXT-X-PLAYLIST-TYPE:VOD\n"
            "#EXTINF:4.000000,\n"
            "seg_00000.ts\n"
            "#EXTINF:4.000000,\n"
            "seg_00001.ts\n"
            "#EXT-X-ENDLIST\n",
            encoding="utf-8",
        )
        (pasta.parent / "master.m3u8").write_text(
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\n"
            "720p/index.m3u8\n",
            encoding="utf-8",
        )

    def test_playlist_preserva_as_tags_hls(self):
        self.client.force_login(self.medico_user)
        token = sign_payload(
            {
                "v": self.video.pk,
                "e": self.enrollment.pk,
                "q": "720p",
                "k": "variant",
                "exp": timezone.now().timestamp() + 60,
            }
        )
        resposta = self.client.get(
            f"/api/media/{self.video.pk}/720p/index.m3u8?token={token}"
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.content.decode()

        # Tags obrigatórias para o hls.js montar a linha do tempo
        self.assertIn("#EXTM3U", corpo)
        self.assertIn("#EXT-X-TARGETDURATION:4", corpo)
        self.assertIn("#EXTINF:4.000000,", corpo)
        self.assertIn("#EXT-X-ENDLIST", corpo)
        # Dois segmentos, assinados
        segmentos = [l for l in corpo.splitlines() if l.startswith("/api/media/")]
        self.assertEqual(len(segmentos), 2)
        self.assertIn("token=", segmentos[0])
        # O instante inicial do segmento precisa vir no token
        payload = verify_token(segmentos[0].split("token=")[1])
        self.assertEqual(payload["s"], 0.0)
        payload2 = verify_token(segmentos[1].split("token=")[1])
        self.assertEqual(payload2["s"], 4.0)


class TestEntregaDoSegmento(TestCase):
    def setUp(self):
        self.medico_user = User.objects.create_user("medico2", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.medico_user,
            cpf="22233344455",
            nome="Medico 2",
            must_change_password=False,
        )
        self.curso = Course.objects.create(title="Curso", status=Course.Status.ACTIVE)
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula 1", duration=100
        )
        self.enrollment = Enrollment.objects.create(
            medico=self.medico, course=self.curso
        )

    def test_segmento_alem_do_limite_e_bloqueado(self):
        self.client.force_login(self.medico_user)
        token = sign_payload(
            {
                "v": self.video.id,
                "e": self.enrollment.id,
                "q": "720p",
                "n": "seg_00050.ts",
                "k": "segment",
                "s": 200.0,
                "exp": timezone.now().timestamp() + 60,
            }
        )
        resposta = self.client.get(
            f"/api/media/{self.video.id}/720p/seg_00050.ts?token={token}"
        )
        self.assertEqual(resposta.status_code, 403)

    def test_segmento_dentro_do_limite_e_liberado(self):
        self.client.force_login(self.medico_user)
        Progress.objects.create(
            enrollment=self.enrollment,
            video=self.video,
            furthest=120,
            covered=120,
        )
        token = sign_payload(
            {
                "v": self.video.id,
                "e": self.enrollment.id,
                "q": "720p",
                "n": "seg_00001.ts",
                "k": "segment",
                "s": 4.0,
                "exp": timezone.now().timestamp() + 60,
            }
        )
        resposta = self.client.get(
            f"/api/media/{self.video.id}/720p/seg_00001.ts?token={token}"
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("X-Accel-Redirect", resposta.headers)

    def test_segmento_assinado_funciona_sem_login_nem_header(self):
        """O token da URL basta: proxies podem remover cookie/Authorization."""
        Progress.objects.create(
            enrollment=self.enrollment,
            video=self.video,
            furthest=120,
            covered=120,
        )
        token = sign_payload(
            {
                "v": self.video.id,
                "e": self.enrollment.id,
                "q": "720p",
                "n": "seg_00001.ts",
                "k": "segment",
                "s": 4.0,
                "exp": timezone.now().timestamp() + 60,
            }
        )
        resposta = self.client.get(
            f"/api/media/{self.video.id}/720p/seg_00001.ts?token={token}"
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("X-Accel-Redirect", resposta.headers)

    def test_token_de_outro_video_e_recusado(self):
        self.client.force_login(self.medico_user)
        Progress.objects.create(
            enrollment=self.enrollment,
            video=self.video,
            furthest=120,
            covered=120,
        )
        token = sign_payload(
            {
                "v": 999,
                "e": self.enrollment.id,
                "q": "720p",
                "n": "seg_00001.ts",
                "k": "segment",
                "s": 4.0,
                "exp": timezone.now().timestamp() + 60,
            }
        )
        resposta = self.client.get(
            f"/api/media/{self.video.id}/720p/seg_00001.ts?token={token}"
        )
        self.assertEqual(resposta.status_code, 403)

    def test_medico_nao_acessa_matricula_de_outro(self):
        outro_user = User.objects.create_user("medico3", password="senha12345")
        outro = Medico.objects.create(
            user=outro_user,
            cpf="33344455566",
            nome="Medico 3",
            must_change_password=False,
        )
        outro_enrollment = Enrollment.objects.create(medico=outro, course=self.curso)

        self.client.force_login(self.medico_user)
        token = sign_payload(
            {
                "v": self.video.id,
                "e": outro_enrollment.id,
                "q": "720p",
                "n": "seg_00001.ts",
                "k": "segment",
                "s": 4.0,
                "exp": timezone.now().timestamp() + 60,
            }
        )
        resposta = self.client.get(
            f"/api/media/{self.video.id}/720p/seg_00001.ts?token={token}"
        )
        self.assertEqual(resposta.status_code, 403)

    def test_player_state_exige_login(self):
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}&video={self.video.id}"
        )
        self.assertEqual(resposta.status_code, 302)

    def test_player_state_limita_retomada_ao_trecho_liberado(self):
        self.client.force_login(self.medico_user)
        Progress.objects.create(
            enrollment=self.enrollment,
            video=self.video,
            last_position=100,
            furthest=0,
            covered=0,
        )
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}&video={self.video.id}"
        )
        self.assertEqual(resposta.status_code, 200)
        estado = resposta.json()
        # last_position alto nao pode virar pedido de segmento proibido.
        self.assertEqual(estado["last_position"], 0)
        self.assertEqual(
            estado["max_allowed"],
            settings.SEGMENT_TOLERANCE_SECONDS
            + settings.HEARTBEAT_INTERVAL_SECONDS,
        )

    def test_heartbeat_manda_voltar_ao_inicio_quando_pede_avanco(self):
        self.client.force_login(self.medico_user)
        Progress.objects.create(
            enrollment=self.enrollment,
            video=self.video,
            last_position=100,
            furthest=0,
            covered=0,
        )
        resposta = self.client.post(
            "/api/player/heartbeat/",
            data=json.dumps(
                {
                    "enrollment": self.enrollment.id,
                    "video": self.video.id,
                    "position": 150,
                    "playing": True,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resposta.status_code, 200)
        # O JS usa authorized_position para rebobinar para um ponto seguro,
        # nunca para os 100 s "fantasma" salvos no progresso.
        self.assertAlmostEqual(
            resposta.json()["authorized_position"], 0, places=1
        )

    def test_manifest_de_video_bloqueado_e_recusado(self):
        """Com desbloqueio sequencial, o vídeo seguinte não pode ser carregado."""
        curso = self.curso
        curso.sequential = True
        curso.save(update_fields=["sequential"])
        video2 = Video.objects.create(
            course=curso, order=2, title="Aula 2", duration=100
        )
        self.client.force_login(self.medico_user)

        # O vídeo 1 não foi assistido: o 2 continua bloqueado.
        resposta = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}&video={video2.id}"
        )
        estado = resposta.json()
        self.assertFalse(estado["unlocked"])
        self.assertEqual(estado["manifest_url"], "")

        token = sign_payload(
            {
                "v": video2.id,
                "e": self.enrollment.id,
                "k": "master",
                "exp": timezone.now().timestamp() + 60,
            }
        )
        resposta = self.client.get(f"/api/media/{video2.id}/master.m3u8?token={token}")
        self.assertEqual(resposta.status_code, 403)
