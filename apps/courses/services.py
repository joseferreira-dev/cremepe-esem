"""Regras de negócio do progresso: cobertura, bloqueio de avanço e aprovação.

O Principle: o servidor é a fonte da verdade. O cliente informa a posição, mas
o servidor decide até onde o aluno pode ir, usando uma "margem de avanço"
acumulada apenas em tempo real de reprodução.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import AuditLog, Enrollment, Progress, Video

# Margem máxima acumulada (segundos). Evita que uma sessão esquecida libere
# um salto grande depois.
MAX_ALLOWANCE = 120.0
# Tolerância entre faixas ao mesclar (evita micro-fragmentação).
INTERVAL_GAP = 0.5


def merge_intervals(intervals) -> list[list[float]]:
    """Une faixas [inicio, fim] sobrepostas/adjacentes e ordena."""
    clean = []
    for item in intervals or []:
        try:
            start, end = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if end > start:
            clean.append([start, end])
    if not clean:
        return []

    clean.sort()
    merged = [clean[0]]
    for start, end in clean[1:]:
        last = merged[-1]
        if start <= last[1] + INTERVAL_GAP:
            last[1] = max(last[1], end)
        else:
            merged.append([start, end])
    return merged


def covered_seconds(intervals) -> float:
    return float(sum(end - start for start, end in (intervals or [])))


def course_videos(course) -> list[Video]:
    """Vídeos disponíveis ao aluno (prontos ou com substituição em andamento)."""
    return [v for v in course.videos.all() if v.disponivel]


def renumerar_videos(course) -> None:
    """Compacta a ordem dos vídeos do curso para 1..N.

    Preserva a sequência exibida (order, id) e elimina buracos ou empates
    deixados por exclusões e edições. Aceita o Curso ou seu pk.
    """
    with transaction.atomic():
        videos = list(Video.objects.filter(course=course).order_by("order", "id"))
        for numero, video in enumerate(videos, start=1):
            if video.order != numero:
                # update() direto: não dispara save() nem sinais (sem recursão).
                Video.objects.filter(pk=video.pk).update(order=numero)
                video.order = numero


def posicionar_video(video: Video, posicao: int) -> None:
    """Coloca `video` na `posicao` (1-based) da sequência do curso e renumera
    todos os vídeos do curso para 1..N.

    Inclusão: o novo vídeo entra exatamente na posição pedida, empurrando os
    seguintes. Edição: mover para cima ou para baixo posiciona no número
    informado — sem deixar buraco nem empate.
    """
    with transaction.atomic():
        outros = list(
            Video.objects.filter(course=video.course_id)
            .exclude(pk=video.pk)
            .order_by("order", "id")
        )
        # Fora da faixa válida: entra no topo (1) ou no fim (len + 1).
        indice = min(max(int(posicao), 1), len(outros) + 1) - 1
        sequencia = outros[:indice] + [video] + outros[indice:]
        for numero, alvo in enumerate(sequencia, start=1):
            if alvo.order != numero:
                Video.objects.filter(pk=alvo.pk).update(order=numero)
                alvo.order = numero


def course_coverage(enrollment: Enrollment) -> float:
    """Percentual (0-100) do curso efetivamente visto pelo médico."""
    videos = course_videos(enrollment.course)
    total = sum(video.duration or 0 for video in videos)
    if total <= 0:
        return 0.0
    covered = 0.0
    progresses = Progress.objects.filter(enrollment=enrollment, video__in=videos)
    for progress in progresses:
        covered += progress.covered or 0
    return min(100.0, (covered / total) * 100.0)


def video_coverage(progress: Progress) -> float:
    duration = progress.video.duration or 0
    if duration <= 0:
        return 0.0
    return min(100.0, ((progress.covered or 0) / duration) * 100.0)


def is_video_unlocked(enrollment: Enrollment, video: Video) -> bool:
    """Vídeos são liberados na ordem quando o curso é sequencial."""
    if not enrollment.course.sequential:
        return True
    previous = None
    for candidato in course_videos(enrollment.course):
        if candidato.order < video.order:
            previous = candidato
        else:
            break
    if previous is None:
        return True
    progress = Progress.objects.filter(enrollment=enrollment, video=previous).first()
    if progress is None:
        return False
    threshold = float(enrollment.course.unlock_threshold) / 100.0
    return video_coverage(progress) >= threshold * 100.0


def unlocked_videos(enrollment: Enrollment) -> list[Video]:
    unlocked = []
    for video in course_videos(enrollment.course):
        if is_video_unlocked(enrollment, video):
            unlocked.append(video)
    return unlocked


def apply_heartbeat(
    enrollment: Enrollment,
    video: Video,
    position: float,
    playing: bool = False,
    now=None,
) -> dict:
    """Processa a batida do player e devolve o estado autorizado.

    Regras:
      * A margem de avanço só cresce enquanto o player reporta reprodução.
      * A posição só avança no máximo pela margem acumulada.
      * Voltar no tempo é livre (reassistir), mas não gera crédito novo.
    """
    now = now or timezone.now()
    duration = video.duration or 0.0

    try:
        position = float(position)
    except (TypeError, ValueError):
        position = 0.0
    position = max(0.0, position)
    if duration > 0:
        position = min(position, duration)

    with transaction.atomic():
        progress, _ = Progress.objects.select_for_update().get_or_create(
            enrollment=enrollment,
            video=video,
            defaults={"intervals": [], "last_seen_at": now},
        )

        # A posicao salva nunca pode estar a frente do trecho realmente
        # liberado. Se estiver (dado antigo/corrompido), o heartbeat devolveria
        # uma posicao que o endpoint de segmento recusa, criando loop de 403.
        last_position = min(progress.last_position or 0.0, progress.furthest or 0.0)
        allowance = progress.allowance or 0.0

        if progress.last_seen_at is None:
            elapsed = max(0.0, (now - progress.created_at).total_seconds())
        else:
            elapsed = max(0.0, (now - progress.last_seen_at).total_seconds())

        # Tempo só conta quando o player reporta que está reproduzindo.
        # Como o player envia heartbeat a cada ~10s, a janela de erro é pequena.
        # O heartbeat do fim do vídeo (evento ended) chega com playing=False,
        # mas a posição avançou exatamente o intervalo de relógio — e esse
        # avanço prova a reprodução tanto quanto o próprio playing=True. Sem
        # isso, vídeos mais curtos que ~2 batidas nunca chegariam a 95% e o
        # aluno ficaria preso no meio do curso. Quem tenta burlar não ganha
        # nada com isso: o crédito continua amarrado ao relógio (o mesmo que
        # já conseguiria enviando playing=True) e saltos continuam recusados.
        if playing or (position - last_position) >= elapsed:
            allowance += elapsed * settings.PROGRESS_MAX_RATE
        allowance = min(allowance, MAX_ALLOWANCE)

        delta = position - last_position
        suspicious = False
        credit = 0.0

        if delta < 0:
            # Voltou no tempo: permitido, sem crédito.
            new_position = position
        else:
            credit = min(delta, allowance)
            new_position = last_position + credit
            if delta > credit + 0.5:
                suspicious = True
                progress.suspicious = (progress.suspicious or 0) + 1

        allowance = max(0.0, allowance - credit)

        if credit > 0:
            intervals = merge_intervals(
                list(progress.intervals or []) + [[last_position, new_position]]
            )
            progress.intervals = intervals
            progress.covered = covered_seconds(intervals)
            progress.furthest = max(progress.furthest or 0.0, new_position)
            progress.watch_seconds = (progress.watch_seconds or 0) + credit

        progress.last_position = new_position
        progress.allowance = allowance
        progress.last_playing = bool(playing)
        progress.last_seen_at = now

        if (
            duration > 0
            and progress.completed_at is None
            and (progress.furthest or 0) >= duration - 1.0
            and (progress.covered or 0) >= duration * 0.95
        ):
            progress.completed_at = now

        progress.save()

    return {
        "progress": progress,
        "authorized_position": new_position,
        "suspicious": suspicious,
        "max_allowed": (progress.furthest or 0.0)
        + settings.SEGMENT_TOLERANCE_SECONDS,
    }


def refresh_enrollment_status(enrollment: Enrollment, actor=None) -> Enrollment:
    """Atualiza a situação da matrícula conforme a cobertura atingida."""
    required = float(enrollment.min_percent or 0)
    coverage = course_coverage(enrollment)
    enrollment.deadline = enrollment.deadline or enrollment.course.deadline

    if enrollment.status in {Enrollment.Status.CANCELLED, Enrollment.Status.REJECTED}:
        return enrollment

    if coverage >= required:
        if enrollment.status != Enrollment.Status.APPROVED:
            enrollment.status = Enrollment.Status.APPROVED
            enrollment.approved_at = timezone.now()
            enrollment.approved_by = actor
            enrollment.save(update_fields=["status", "approved_at", "approved_by", "deadline"])
            record_audit(
                "aprovacao_automatica",
                actor=actor,
                target=f"Matrícula #{enrollment.id}",
                detail={"cobertura": round(coverage, 2), "exigido": required},
            )
    elif enrollment.status == Enrollment.Status.APPROVED:
        # Regressão (ex.: reset de progresso) volta para em andamento.
        enrollment.status = Enrollment.Status.IN_PROGRESS
        enrollment.approved_at = None
        enrollment.approved_by = None
        enrollment.save(update_fields=["status", "approved_at", "approved_by"])
    elif enrollment.status == Enrollment.Status.ENROLLED and coverage > 0:
        enrollment.status = Enrollment.Status.IN_PROGRESS
        enrollment.save(update_fields=["status"])

    return enrollment


def reset_progress(enrollment: Enrollment, actor=None) -> None:
    Progress.objects.filter(enrollment=enrollment).delete()
    enrollment.status = Enrollment.Status.ENROLLED
    enrollment.approved_at = None
    enrollment.approved_by = None
    enrollment.save(update_fields=["status", "approved_at", "approved_by"])
    record_audit(
        "reset_progresso",
        actor=actor,
        target=f"Matrícula #{enrollment.id}",
        detail={"medico": enrollment.medico.nome, "curso": enrollment.course.title},
    )


def reset_video_progress(video: Video, actor=None) -> int:
    """Apaga o progresso de um vídeo substituído por outro conteúdo.

    Usado quando a substituição é de um vídeo totalmente diferente: o tempo
    assistido não faz sentido na nova gravação. Devolve quantos registros
    foram removidos.
    """
    matriculas = list(
        Enrollment.objects.filter(progress_records__video=video).distinct()
    )
    removidos, _ = Progress.objects.filter(video=video).delete()
    for matricula in matriculas:
        refresh_enrollment_status(matricula, actor=actor)
    return removidos


def record_audit(
    action: str,
    actor=None,
    target: str = "",
    detail: dict | None = None,
    ip: str | None = None,
) -> AuditLog:
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target=target,
        detail=detail or {},
        ip=ip,
    )


def format_hours(seconds: float) -> str:
    seconds = int(seconds or 0)
    hours, resto = divmod(seconds, 3600)
    minutos, _ = divmod(resto, 60)
    if hours:
        return f"{hours}h{minutos:02d}min"
    return f"{minutos}min"


def percent(value: float) -> Decimal:
    return Decimal(str(round(float(value or 0), 2)))
