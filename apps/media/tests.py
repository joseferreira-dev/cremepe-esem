"""Testes de upload em blocos, assinatura de URLs e entrega do vídeo."""

import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Medico
from apps.accounts.tokens import gerar_token_api
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

    def test_pagina_de_upload_define_config_antes_do_script(self):
        self.client.force_login(self.admin)
        html = self.client.get("/api/uploads/pagina/").content.decode()
        self.assertIn("window.CREMEPE_UPLOAD", html)
        self.assertIn("/api/uploads/init/", html)
        self.assertIn("apiToken", html)
        self.assertLess(
            html.index("window.CREMEPE_UPLOAD"),
            html.index("js/upload"),
            "a configuração precisa existir antes do upload.js",
        )

    def test_upload_exige_cabecalho_ajax(self):
        self.client.force_login(self.admin)
        resposta = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(
                {"video_id": self.video.id, "filename": "aula.mp4", "size": 100}
            ),
            content_type="application/json",
        )
        self.assertEqual(resposta.status_code, 403)

    def test_upload_sem_sessao_responde_json(self):
        resposta = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(
                {"video_id": self.video.id, "filename": "aula.mp4", "size": 0}
            ),
            content_type="application/json",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(resposta.status_code, 401)
        self.assertIn("Sessão expirada", resposta.json()["error"])

    def test_upload_autentica_por_token_sem_cookie(self):
        token = gerar_token_api(self.admin, validade=60)
        resposta = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(
                {"video_id": self.video.id, "filename": "aula.mp4", "size": 0}
            ),
            content_type="application/json",
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "X-API-Token": token,
            },
        )
        # Passou pela autenticação e chegou à validação do tamanho.
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Tamanho", resposta.json()["error"])

    def test_upload_autentica_por_token_na_url(self):
        token = gerar_token_api(self.admin, validade=60)
        resposta = self.client.post(
            f"/api/uploads/init/?api_token={token}",
            data=json.dumps(
                {"video_id": self.video.id, "filename": "aula.mp4", "size": 0}
            ),
            content_type="application/json",
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Tamanho", resposta.json()["error"])

    def test_token_de_admin_vence_sessao_de_medico(self):
        self.client.force_login(self.medico_user)
        token = gerar_token_api(self.admin, validade=60)
        resposta = self.client.post(
            f"/api/uploads/init/?api_token={token}",
            data=json.dumps(
                {"video_id": self.video.id, "filename": "aula.mp4", "size": 0}
            ),
            content_type="application/json",
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Tamanho", resposta.json()["error"])

    def test_upload_completo_em_blocos(self):
        self.client.force_login(self.admin)
        conteudo = b"x" * 1000

        resposta = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(
                {"video_id": self.video.id, "filename": "aula.mp4", "size": len(conteudo)}
            ),
            content_type="application/json",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        upload_id = dados["upload_id"]
        self.assertEqual(dados["total_chunks"], 1)

        resposta = self.client.post(
            f"/api/uploads/{upload_id}/chunk/",
            data=conteudo,
            content_type="application/octet-stream",
            headers={
                "X-Chunk-Index": "0",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        self.assertEqual(resposta.status_code, 200)

        resposta = self.client.post(
            f"/api/uploads/{upload_id}/complete/",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
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
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        upload_id = resposta.json()["upload_id"]
        resposta = self.client.post(
            f"/api/uploads/{upload_id}/chunk/",
            data=b"curto",
            content_type="application/octet-stream",
            headers={
                "X-Chunk-Index": "0",
                "X-Requested-With": "XMLHttpRequest",
            },
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
            headers={"X-Requested-With": "XMLHttpRequest"},
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
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        upload_id = resposta.json()["upload_id"]
        resposta = self.client.post(
            f"/api/uploads/{upload_id}/complete/",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
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
            headers={"X-Requested-With": "XMLHttpRequest"},
        ).json()

        # Simula queda no meio: grava o primeiro bloco e reabre a sessão.
        self.client.post(
            f"/api/uploads/{primeira['upload_id']}/chunk/",
            data=b"x" * 1000,
            content_type="application/octet-stream",
            headers={
                "X-Chunk-Index": "0",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        segunda = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ).json()

        self.assertEqual(segunda["upload_id"], primeira["upload_id"])
        self.assertTrue(segunda.get("retomado"))
        self.assertEqual(segunda["received"], [0])


class TestSubstituicaoDeVideo(TestCase):
    """Regra 3: a versão antiga fica disponível até a nova terminar."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin-sub", "a@b.c", "senha12345")
        self.user = User.objects.create_user("medico-sub", password="x")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="88899900011",
            nome="Medico Sub",
            must_change_password=False,
        )
        self.curso = Course.objects.create(
            title="Curso Sub", status=Course.Status.ACTIVE, min_percent=50
        )
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula", duration=100,
            status=Video.Status.READY, hls_path=f"hls/{self.curso.id}",
        )
        self.matricula = Enrollment.objects.create(
            medico=self.medico, course=self.curso, min_percent=50
        )
        Progress.objects.create(
            enrollment=self.matricula, video=self.video,
            covered=100, furthest=100, intervals=[[0, 100]],
        )
        self.client.force_login(self.admin)

    def test_promote_hls_troca_pendente_por_oficial(self):
        from apps.media.services import hls_dir, hls_pending_dir, promote_hls

        oficial = hls_dir(self.video)
        pendente = hls_pending_dir(self.video)
        oficial.mkdir(parents=True, exist_ok=True)
        (oficial / "master.m3u8").write_text("#EXTM3U\n# antigo\n", encoding="utf-8")
        pendente.mkdir(parents=True, exist_ok=True)
        (pendente / "master.m3u8").write_text("#EXTM3U\n# novo\n", encoding="utf-8")

        promote_hls(self.video)

        self.assertFalse(pendente.exists())
        self.assertEqual(
            (oficial / "master.m3u8").read_text(encoding="utf-8"),
            "#EXTM3U\n# novo\n",
        )

    def test_reset_video_progress_apaga_progresso_e_mantem_matricula(self):
        from apps.courses.services import reset_video_progress

        removidos = reset_video_progress(self.video)
        self.assertEqual(removidos, 1)
        self.assertEqual(Progress.objects.filter(video=self.video).count(), 0)
        self.assertTrue(Enrollment.objects.filter(pk=self.matricula.pk).exists())
        self.matricula.refresh_from_db()
        # Abaixo do mínimo após reset, mas não aprovado/reprovado manualmente.
        self.assertNotEqual(self.matricula.status, Enrollment.Status.APPROVED)

    def test_upload_substituicao_registra_tipo_no_sessao(self):
        payload = {
            "video_id": self.video.pk,
            "filename": "novo.mp4",
            "size": 100,
            "substituicao": "video_diferente",
        }
        resposta = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(resposta.status_code, 200)
        from apps.media.models import UploadSession

        sessao = UploadSession.objects.get(pk=resposta.json()["upload_id"])
        self.assertEqual(sessao.substituicao, Video.Substituicao.VIDEO_DIFERENTE)

    def test_upload_substituicao_ignorada_para_video_sem_media(self):
        novo = Video.objects.create(
            course=self.curso, order=2, title="Novo", status=Video.Status.EMPTY
        )
        payload = {
            "video_id": novo.pk,
            "filename": "novo.mp4",
            "size": 100,
            "substituicao": "video_diferente",
        }
        resposta = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(resposta.status_code, 200)
        from apps.media.models import UploadSession

        sessao = UploadSession.objects.get(pk=resposta.json()["upload_id"])
        self.assertEqual(sessao.substituicao, Video.Substituicao.NENHUMA)

    def test_upload_substituicao_invalida_rejeitada(self):
        payload = {
            "video_id": self.video.pk,
            "filename": "novo.mp4",
            "size": 100,
            "substituicao": "qualquer",
        }
        resposta = self.client.post(
            "/api/uploads/init/",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(resposta.status_code, 400)


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
            course=self.curso, order=1, title="Aula 1", duration=100,
            status=Video.Status.READY,
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
        # nunca para os 100 s "fantasma" salvos no progresso. Aceita-se uma
        # margem mínima de avanço proporcional ao tempo realmente decorrido.
        self.assertLess(resposta.json()["authorized_position"], 1)

    def test_manifest_de_video_bloqueado_e_recusado(self):
        """Com desbloqueio sequencial, o vídeo seguinte não pode ser carregado."""
        curso = self.curso
        curso.sequential = True
        curso.save(update_fields=["sequential"])
        video2 = Video.objects.create(
            course=curso, order=2, title="Aula 2", duration=100,
            status=Video.Status.READY,
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


class TestJornadaDeConclusaoNoPlayer(TestCase):
    """A sequência real do player no fim de uma aula de 15 s.

    Reproduz o estado imediatamente anterior ao heartbeat do fim (10 s
    creditados no primeiro intervalo, margem ociosa de 2,5 s e 5 s desde a
    última batida) e confirma que a resposta devolve a conclusão, libera a
    próxima aula e aprova a matrícula — é o que o player usa para atualizar
    a lista lateral sem recarregar a página.
    """

    def setUp(self):
        self.user = User.objects.create_user("medico-jornada", password="senha12345")
        self.medico = Medico.objects.create(
            user=self.user,
            cpf="44433322211",
            nome="Medico Jornada",
            must_change_password=False,
        )
        self.curso = Course.objects.create(
            title="Curso curto",
            status=Course.Status.ACTIVE,
            sequential=True,
            unlock_threshold=95,
            min_percent=50,
        )
        self.video1 = Video.objects.create(
            course=self.curso, order=1, title="Aula 1", duration=15,
            status=Video.Status.READY,
        )
        self.video2 = Video.objects.create(
            course=self.curso, order=2, title="Aula 2", duration=15,
            status=Video.Status.READY,
        )
        self.enrollment = Enrollment.objects.create(
            medico=self.medico, course=self.curso, min_percent=50
        )
        self.client.force_login(self.user)

    def _progresso_antes_do_fim(self):
        # Estado real logo antes do heartbeat do fim de uma aula de 15 s.
        Progress.objects.create(
            enrollment=self.enrollment,
            video=self.video1,
            intervals=[[0.0, 10.0]],
            covered=10.0,
            furthest=10.0,
            last_position=10.0,
            allowance=2.5,
            last_seen_at=timezone.now() - timezone.timedelta(seconds=5),
        )

    def _heartbeat(self, video, position, playing):
        resposta = self.client.post(
            "/api/player/heartbeat/",
            data=json.dumps(
                {
                    "enrollment": self.enrollment.id,
                    "video": video.id,
                    "position": position,
                    "playing": playing,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resposta.status_code, 200)
        return resposta.json()

    def test_fim_da_aula_libera_a_proxima_e_aprova_sem_reload(self):
        self._progresso_antes_do_fim()
        dados = self._heartbeat(self.video1, position=15, playing=False)

        self.assertTrue(dados["completed"])
        self.assertTrue(dados["next_unlocked"])
        self.assertEqual(dados["enrollment_status"], Enrollment.Status.APPROVED)
        self.assertGreaterEqual(dados["covered"], 15 * 0.95)

        # A próxima aula responde unlocked no endpoint de estado do player.
        estado = self.client.get(
            f"/api/player/state/?enrollment={self.enrollment.id}"
            f"&video={self.video2.id}"
        ).json()
        self.assertTrue(estado["unlocked"])

    def test_antes_de_concluir_a_proxima_continua_bloqueada(self):
        self._progresso_antes_do_fim()
        # Batida comum do intervalo (posição inalterada): nada conclui ainda.
        dados = self._heartbeat(self.video1, position=10, playing=True)

        self.assertFalse(dados["completed"])
        self.assertFalse(dados["next_unlocked"])
        self.assertNotEqual(dados["enrollment_status"], Enrollment.Status.APPROVED)
