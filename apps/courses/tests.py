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
    refresh_enrollment_status,
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
            course=self.curso, order=1, title="Aula 1", duration=1000
        )
        self.video2 = Video.objects.create(
            course=self.curso, order=2, title="Aula 2", duration=1000
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
            course=self.curso, order=1, title="Aula 1", duration=1000
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
