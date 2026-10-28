"""Testes das regras de progresso, desbloqueio e aprovação."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Medico
from apps.courses.models import Course, Enrollment, Progress, Video
from apps.courses.services import (
    apply_heartbeat,
    course_coverage,
    is_video_unlocked,
    merge_intervals,
    posicionar_video,
    refresh_enrollment_status,
    renumerar_videos,
    reset_progress,
)


def criar_medico(cpf="11122233344", nome="Médico Teste"):
    user = User.objects.create_user(username=f"medico-{cpf}", password="x")
    return Medico.objects.create(user=user, cpf=cpf, nome=nome)


class TestMergeIntervals(TestCase):
    def test_une_intervalos_sobrepostos(self):
        resultado = merge_intervals([[0, 10], [5, 15], [20, 30]])
        self.assertEqual(resultado, [[0, 15], [20, 30]])

    def test_descarta_intervalos_invertidos(self):
        self.assertEqual(merge_intervals([[10, 5]]), [])

    def test_lista_vazia(self):
        self.assertEqual(merge_intervals([]), [])


class TestHeartbeat(TestCase):
    def setUp(self):
        self.medico = criar_medico()
        self.curso = Course.objects.create(
            title="Ética Médica", status=Course.Status.ACTIVE, min_percent=50
        )
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula 1", duration=3600, status=Video.Status.READY
        )
        self.enrollment = Enrollment.objects.create(
            medico=self.medico, course=self.curso, min_percent=50
        )

    def test_credita_tempo_de_reproducao_real(self):
        agora = timezone.now()
        apply_heartbeat(
            self.enrollment, self.video, position=0, playing=True, now=agora
        )
        resultado = apply_heartbeat(
            self.enrollment,
            self.video,
            position=10,
            playing=True,
            now=agora + timedelta(seconds=10),
        )
        self.assertAlmostEqual(resultado["authorized_position"], 10, delta=1)
        self.assertAlmostEqual(resultado["progress"].furthest, 10, delta=1)

    def test_recusa_salto_de_tempo(self):
        agora = timezone.now()
        apply_heartbeat(
            self.enrollment, self.video, position=0, playing=True, now=agora
        )
        resultado = apply_heartbeat(
            self.enrollment,
            self.video,
            position=1800,
            playing=True,
            now=agora + timedelta(seconds=5),
        )
        self.assertLess(resultado["authorized_position"], 30)
        self.assertTrue(resultado["suspicious"])
        self.assertEqual(resultado["progress"].suspicious, 1)

    def test_pausa_nao_acumula_avanco(self):
        agora = timezone.now()
        apply_heartbeat(
            self.enrollment, self.video, position=0, playing=True, now=agora
        )
        # O player fica pausado durante 10 minutos reportando playing=False.
        apply_heartbeat(
            self.enrollment,
            self.video,
            position=0,
            playing=False,
            now=agora + timedelta(minutes=10),
        )
        # Ao retomar, tenta saltar para o final: deve ser recusado.
        resultado = apply_heartbeat(
            self.enrollment,
            self.video,
            position=3500,
            playing=True,
            now=agora + timedelta(minutes=10, seconds=1),
        )
        self.assertLess(resultado["authorized_position"], 60)

    def test_voltar_no_tempo_e_permitido(self):
        agora = timezone.now()
        apply_heartbeat(
            self.enrollment, self.video, position=0, playing=True, now=agora
        )
        apply_heartbeat(
            self.enrollment,
            self.video,
            position=60,
            playing=True,
            now=agora + timedelta(seconds=60),
        )
        resultado = apply_heartbeat(
            self.enrollment,
            self.video,
            position=10,
            playing=True,
            now=agora + timedelta(seconds=61),
        )
        self.assertEqual(resultado["authorized_position"], 10)
        self.assertGreaterEqual(resultado["progress"].furthest, 60)

    def test_intervalos_sao_registrados(self):
        agora = timezone.now()
        apply_heartbeat(
            self.enrollment, self.video, position=0, playing=True, now=agora
        )
        apply_heartbeat(
            self.enrollment,
            self.video,
            position=30,
            playing=True,
            now=agora + timedelta(seconds=30),
        )
        progress = Progress.objects.get(enrollment=self.enrollment, video=self.video)
        self.assertGreaterEqual(progress.covered, 29)
        self.assertEqual(len(progress.intervals), 1)


class TestDesbloqueio(TestCase):
    def setUp(self):
        self.medico = criar_medico("55566677788")
        self.curso = Course.objects.create(
            title="Ética Médica",
            status=Course.Status.ACTIVE,
            sequential=True,
            unlock_threshold=95,
        )
        self.video1 = Video.objects.create(
            course=self.curso, order=1, title="Aula 1", duration=1000,
            status=Video.Status.READY,
        )
        self.video2 = Video.objects.create(
            course=self.curso, order=2, title="Aula 2", duration=1000,
            status=Video.Status.READY,
        )
        self.enrollment = Enrollment.objects.create(
            medico=self.medico, course=self.curso
        )

    def test_primeiro_video_sempre_liberado(self):
        self.assertTrue(is_video_unlocked(self.enrollment, self.video1))

    def test_segundo_video_bloqueado_ate_completar_o_primeiro(self):
        self.assertFalse(is_video_unlocked(self.enrollment, self.video2))

    def test_segundo_video_liberado_apos_completar_o_primeiro(self):
        Progress.objects.create(
            enrollment=self.enrollment,
            video=self.video1,
            furthest=1000,
            covered=1000,
            last_position=1000,
        )
        self.assertTrue(is_video_unlocked(self.enrollment, self.video2))

    def test_curso_nao_sequencial_libera_tudo(self):
        self.curso.sequential = False
        self.curso.save(update_fields=["sequential"])
        self.assertTrue(is_video_unlocked(self.enrollment, self.video2))


class TestAprovacao(TestCase):
    def setUp(self):
        self.medico = criar_medico("99900011122")
        self.curso = Course.objects.create(
            title="Ética Médica", status=Course.Status.ACTIVE, min_percent=50
        )
        self.video = Video.objects.create(
            course=self.curso, order=1, title="Aula 1", duration=1000,
            status=Video.Status.READY,
        )
        self.enrollment = Enrollment.objects.create(
            medico=self.medico, course=self.curso, min_percent=50
        )

    def test_cobertura_do_curso(self):
        Progress.objects.create(
            enrollment=self.enrollment,
            video=self.video,
            furthest=600,
            covered=600,
            last_position=600,
        )
        self.assertAlmostEqual(course_coverage(self.enrollment), 60.0, delta=0.1)

    def test_aprovacao_automatica(self):
        Progress.objects.create(
            enrollment=self.enrollment,
            video=self.video,
            furthest=600,
            covered=600,
            last_position=600,
        )
        refresh_enrollment_status(self.enrollment)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, Enrollment.Status.APPROVED)
        self.assertIsNotNone(self.enrollment.approved_at)

    def test_nao_aprovado_abaixo_do_minimo(self):
        Progress.objects.create(
            enrollment=self.enrollment,
            video=self.video,
            furthest=100,
            covered=100,
            last_position=100,
        )
        refresh_enrollment_status(self.enrollment)
        self.enrollment.refresh_from_db()
        self.assertNotEqual(self.enrollment.status, Enrollment.Status.APPROVED)

    def test_reset_de_progresso(self):
        Progress.objects.create(
            enrollment=self.enrollment,
            video=self.video,
            furthest=600,
            covered=600,
        )
        reset_progress(self.enrollment)
        self.assertEqual(Progress.objects.filter(enrollment=self.enrollment).count(), 0)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, Enrollment.Status.ENROLLED)


class TestRenumeracaoDeVideos(TestCase):
    """A numeração se ajusta sozinha: inclusão, edição e exclusão."""

    def setUp(self):
        self.curso = Course.objects.create(
            title="Curso renumeração", status=Course.Status.ACTIVE
        )
        self.videos = [
            Video.objects.create(course=self.curso, order=ordem, title=f"Aula {ordem}")
            for ordem in (1, 2, 3)
        ]

    def sequencia(self):
        return [(v.title, v.order) for v in self.curso.videos.order_by("order", "id")]

    def test_renumerar_compata_buracos_preservando_a_sequencia(self):
        Video.objects.filter(title="Aula 2").update(order=8)
        renumerar_videos(self.curso)
        self.assertEqual(
            self.sequencia(), [("Aula 1", 1), ("Aula 3", 2), ("Aula 2", 3)]
        )

    def test_renumerar_resolve_empates_e_aceita_pk(self):
        Video.objects.filter(title="Aula 3").update(order=2)
        renumerar_videos(self.curso.pk)
        self.assertEqual(
            self.sequencia(), [("Aula 1", 1), ("Aula 2", 2), ("Aula 3", 3)]
        )

    def test_posicionar_para_baixo_ocupa_a_posicao_pedida(self):
        posicionar_video(Video.objects.get(title="Aula 1"), 3)
        self.assertEqual(
            self.sequencia(), [("Aula 2", 1), ("Aula 3", 2), ("Aula 1", 3)]
        )

    def test_posicionar_fora_da_faixa_entra_no_topo_ou_no_fim(self):
        posicionar_video(Video.objects.get(title="Aula 3"), 0)
        self.assertEqual(
            self.sequencia(), [("Aula 3", 1), ("Aula 1", 2), ("Aula 2", 3)]
        )
        posicionar_video(Video.objects.get(title="Aula 3"), 99)
        self.assertEqual(
            self.sequencia(), [("Aula 1", 1), ("Aula 2", 2), ("Aula 3", 3)]
        )


class TestRenumeracaoNoAdmin(TestCase):
    """O Django admin também mantém a sequência 1..N.

    Cobre a lista editável (save_model), a exclusão em massa
    (delete_queryset) e o inline do curso (save_formset).
    """

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            "admin-renum", "renum@b.c", "senha12345"
        )
        self.curso = Course.objects.create(
            title="Curso admin ordem", status=Course.Status.ACTIVE
        )
        self.videos = [
            Video.objects.create(course=self.curso, order=ordem, title=f"Aula {ordem}")
            for ordem in (1, 2, 3)
        ]
        self.client.force_login(self.admin_user)

    def sequencia(self, curso=None):
        curso = curso or self.curso
        return [(v.title, v.order) for v in curso.videos.order_by("order", "id")]

    def test_lista_editavel_posiciona_e_renumera(self):
        # Aula 1 (topo) passa para a última posição, pela lista do admin.
        dados = {
            "form-TOTAL_FORMS": "3",
            "form-INITIAL_FORMS": "3",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for indice, video in enumerate(self.videos):
            dados[f"form-{indice}-id"] = str(video.pk)
            dados[f"form-{indice}-order"] = (
                "3" if video.title == "Aula 1" else str(video.order)
            )
        resposta = self.client.post("/admin/courses/video/", {**dados, "_save": "1"})
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            self.sequencia(), [("Aula 2", 1), ("Aula 3", 2), ("Aula 1", 3)]
        )

    def test_exclusao_em_massa_renumera(self):
        resposta = self.client.post(
            "/admin/courses/video/",
            {
                "action": "delete_selected",
                "_selected_action": [str(self.videos[1].pk)],
                "post": "1",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.sequencia(), [("Aula 1", 1), ("Aula 3", 2)])

    def test_inline_do_curso_edita_e_exclui_com_renumeracao(self):
        aula1, aula2, aula3 = self.videos
        dados = {
            "title": self.curso.title,
            "edition": "",
            "description": "",
            "status": Course.Status.ACTIVE,
            "min_percent": "50.00",
            "unlock_threshold": "95.00",
            "sequential": "on",
            "deadline": "",
            "_save": "1",
            # Prefixos dos inlines vêm do related_name: videos / enrollments.
            "videos-TOTAL_FORMS": "3",
            "videos-INITIAL_FORMS": "3",
            "videos-MIN_NUM_FORMS": "0",
            "videos-MAX_NUM_FORMS": "1000",
            # 2º inline do curso (matrículas): sem alterações.
            "enrollments-TOTAL_FORMS": "0",
            "enrollments-INITIAL_FORMS": "0",
            "enrollments-MIN_NUM_FORMS": "0",
            "enrollments-MAX_NUM_FORMS": "1000",
        }
        # Aula 1 vai para a última posição; Aula 2 é excluída de uma vez.
        linhas = {
            aula1.pk: {"order": "3", "title": aula1.title, "is_required": "on"},
            aula2.pk: {
                "DELETE": "on",
                "order": str(aula2.order),
                "title": aula2.title,
                "is_required": "on",
            },
            aula3.pk: {
                "order": str(aula3.order),
                "title": aula3.title,
                "is_required": "on",
            },
        }
        for indice, video in enumerate(self.videos):
            dados[f"videos-{indice}-id"] = str(video.pk)
            dados[f"videos-{indice}-course"] = str(self.curso.pk)
            for campo, valor in linhas[video.pk].items():
                dados[f"videos-{indice}-{campo}"] = valor
        resposta = self.client.post(
            f"/admin/courses/course/{self.curso.pk}/change/", dados
        )
        if resposta.status_code != 302:
            erros = [repr(dict(resposta.context["adminform"].form.errors))]
            erros += [
                f"{fs.formset.prefix}: {list(fs.formset.non_form_errors())} "
                f"{[dict(f.errors) for f in fs.formset.forms]}"
                for fs in resposta.context.get("inline_admin_formsets", [])
            ]
            self.fail(
                f"esperava 302 (salvo), veio {resposta.status_code}: {'; '.join(erros)}"
            )
        # Aula 2 saiu, Aula 1 foi pedida por último: sequência 1..N.
        self.assertEqual(self.sequencia(), [("Aula 3", 1), ("Aula 1", 2)])
