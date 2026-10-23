"""Views do painel administrativo."""

from __future__ import annotations

import csv
import secrets

from django.contrib import messages
from django.db import transaction
from django.db.models import F, Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.models import Medico
from apps.accounts.services import redefinir_senha
from apps.courses.models import AuditLog, Course, Enrollment, Progress, Video, WatchSession
from apps.courses.services import (
    course_coverage,
    format_hours,
    record_audit,
    reset_progress,
)
from apps.media.services import enqueue_transcode

from .forms import (
    CursoForm,
    MatriculaForm,
    MatriculaLoteForm,
    MedicoForm,
    MedicoLoteForm,
    VideoForm,
)


# --------------------------------------------------------------------------
# Visão geral
# --------------------------------------------------------------------------
def index(request):
    cursos = list(Course.objects.all())
    matriculas = Enrollment.objects.select_related("medico", "course")

    total_medicos = Medico.objects.filter(ativo=True).count()
    ativas = matriculas.exclude(
        status__in=[Enrollment.Status.CANCELLED, Enrollment.Status.REJECTED]
    )
    aprovadas = matriculas.filter(status=Enrollment.Status.APPROVED).count()

    em_andamento = []
    for matricula in ativas.select_related("medico", "course"):
        cobertura = course_coverage(matricula)
        em_andamento.append(
            {
                "matricula": matricula,
                "cobertura": round(cobertura, 1),
                "ultima": Progress.objects.filter(enrollment=matricula)
                .aggregate(last=Max("last_seen_at"))["last"],
            }
        )
    em_andamento.sort(key=lambda item: item["cobertura"], reverse=True)

    faixa_desempenho = [
        {"rotulo": "0–25%", "total": 0, "cor": "bg-slate-300"},
        {"rotulo": "25–50%", "total": 0, "cor": "bg-sky-400"},
        {"rotulo": "50–75%", "total": 0, "cor": "bg-pacifico-500"},
        {"rotulo": "75–100%", "total": 0, "cor": "bg-marca-500"},
    ]
    for item in em_andamento:
        cobertura = item["cobertura"]
        if cobertura < 25:
            faixa_desempenho[0]["total"] += 1
        elif cobertura < 50:
            faixa_desempenho[1]["total"] += 1
        elif cobertura < 75:
            faixa_desempenho[2]["total"] += 1
        else:
            faixa_desempenho[3]["total"] += 1

    videos_processando = list(
        Video.objects.filter(status__in=[Video.Status.QUEUED, Video.Status.PROCESSING])
        .select_related("course")
        .order_by("course__title", "order")
    )
    videos_com_erro = list(
        Video.objects.filter(status=Video.Status.ERROR).select_related("course")
    )

    linhas = []
    for curso in cursos:
        matriculas_curso = list(curso.enrollments.select_related("medico"))
        if not matriculas_curso:
            continue
        media = sum(course_coverage(m) for m in matriculas_curso) / len(matriculas_curso)
        linhas.append(
            {
                "curso": curso,
                "total": len(matriculas_curso),
                "aprovados": sum(
                    1 for m in matriculas_curso if m.status == Enrollment.Status.APPROVED
                ),
                "media": round(media, 1),
                "duracao": format_hours(curso.total_duration),
            }
        )
    linhas.sort(key=lambda item: item["media"], reverse=True)

    return render(
        request,
        "panel/index.html",
        {
            "total_cursos": len(cursos),
            "total_medicos": total_medicos,
            "total_matriculas": ativas.count(),
            "total_aprovados": aprovadas,
            "taxa_aprovacao": round(
                (aprovadas / ativas.count() * 100) if ativas.count() else 0, 1
            ),
            "em_andamento": em_andamento[:8],
            "faixa_desempenho": faixa_desempenho,
            "linhas": linhas,
            "videos_processando": videos_processando,
            "videos_com_erro": videos_com_erro,
            "ultimos_logs": AuditLog.objects.select_related("actor")[:8],
            "sessoes_hoje": WatchSession.objects.filter(
                started_at__date=timezone.now().date()
            ).count(),
        },
    )


# --------------------------------------------------------------------------
# Cursos
# --------------------------------------------------------------------------
def cursos(request):
    busca = request.GET.get("q", "").strip()
    consulta = Course.objects.all()
    if busca:
        consulta = consulta.filter(
            Q(title__icontains=busca) | Q(edition__icontains=busca)
        )

    linhas = []
    for curso in consulta:
        matriculas = curso.enrollments.select_related("medico")
        total = matriculas.count()
        aprovados = matriculas.filter(status=Enrollment.Status.APPROVED).count()
        media = (
            sum(course_coverage(m) for m in matriculas) / total if total else 0
        )
        linhas.append(
            {
                "curso": curso,
                "total": total,
                "aprovados": aprovados,
                "media": round(media, 1),
                "videos": curso.video_count,
                "duracao": format_hours(curso.total_duration),
            }
        )

    return render(request, "panel/cursos.html", {"linhas": linhas, "busca": busca})


def curso_form(request, pk=None):
    curso = get_object_or_404(Course, pk=pk) if pk else Course()
    form = CursoForm(request.POST or None, request.FILES or None, instance=curso)

    if request.method == "POST" and form.is_valid():
        curso = form.save()
        record_audit(
            "curso_salvo" if pk else "curso_criado",
            actor=request.user,
            target=curso.title,
            detail={"id": curso.pk},
            ip=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"Curso “{curso.title}” salvo.")
        return redirect("painel_curso", pk=curso.pk)

    return render(
        request,
        "panel/curso_form.html",
        {"form": form, "curso": curso if pk else None},
    )


def curso_detalhe(request, pk):
    curso = get_object_or_404(Course, pk=pk)
    videos = list(curso.videos.all())

    form_matricula_lote = MatriculaLoteForm(
        request.POST
        if request.method == "POST" and request.POST.get("acao") == "matricular_lote"
        else None,
        curso=curso,
    )
    if request.method == "POST" and request.POST.get("acao") == "matricular_lote":
        if form_matricula_lote.is_valid():
            criadas = form_matricula_lote.save()
            for matricula in criadas:
                record_audit(
                    "matricula_criada",
                    actor=request.user,
                    target=f"{matricula.medico.nome} — {curso.title}",
                    detail={"lote": True},
                    ip=request.META.get("REMOTE_ADDR"),
                )
            messages.success(
                request,
                f"{len(criadas)} matrícula(s) registrada(s) no curso.",
            )
            return redirect("painel_curso", pk=curso.pk)
        messages.error(request, "Não foi possível registrar as matrículas. Confira os dados.")

    linhas = []
    for matricula in curso.enrollments.select_related("medico").order_by("medico__nome"):
        progresses = Progress.objects.filter(enrollment=matricula)
        cobertura = course_coverage(matricula)
        linhas.append(
            {
                "matricula": matricula,
                "cobertura": round(cobertura, 1),
                "exigido": float(matricula.min_percent or 0),
                "aprovado": matricula.status == Enrollment.Status.APPROVED,
                "ultima": progresses.aggregate(last=Max("last_seen_at"))["last"],
                "tempo": format_hours(progresses.aggregate(total=Sum("watch_seconds"))["total"] or 0),
                "sessoes": WatchSession.objects.filter(enrollment=matricula).count(),
            }
        )

    return render(
        request,
        "panel/curso_detalhe.html",
        {
            "curso": curso,
            "videos": videos,
            "linhas": linhas,
            "form_matricula_lote": form_matricula_lote,
            "medicos_disponiveis": form_matricula_lote.fields["medicos"].queryset.count(),
            "duracao": format_hours(curso.total_duration),
        },
    )


@require_POST
def curso_remover(request, pk):
    curso = get_object_or_404(Course, pk=pk)
    if curso.enrollments.exists():
        messages.error(request, "Não é possível excluir um curso com matrículas.")
        return redirect("painel_curso", pk=pk)
    titulo = curso.title
    curso.delete()
    record_audit(
        "curso_excluido", actor=request.user, target=titulo, ip=request.META.get("REMOTE_ADDR")
    )
    messages.success(request, "Curso excluído.")
    return redirect("painel_cursos")


# --------------------------------------------------------------------------
# Vídeos
# --------------------------------------------------------------------------
def video_form(request, pk=None):
    video = get_object_or_404(Video, pk=pk) if pk else Video()
    curso_anterior_id = video.course_id if pk else None
    ordem_anterior = video.order if pk else None
    form = VideoForm(request.POST or None, instance=video)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            novo_video = form.save(commit=False)

            if curso_anterior_id != novo_video.course_id:
                # Mudou de curso ou é novo: abre espaço no curso de destino.
                Video.objects.filter(
                    course=novo_video.course_id, order__gte=novo_video.order
                ).exclude(pk=novo_video.pk).update(order=F("order") + 1)
            elif ordem_anterior != novo_video.order:
                # Mudou a ordem dentro do mesmo curso: empurra os seguintes.
                Video.objects.filter(
                    course=novo_video.course_id, order__gte=novo_video.order
                ).exclude(pk=novo_video.pk).update(order=F("order") + 1)

            novo_video.save()

        video = novo_video
        record_audit(
            "video_salvo" if pk else "video_criado",
            actor=request.user,
            target=video.title,
            detail={"id": video.pk, "ordem": video.order},
            ip=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"Vídeo “{video.title}” salvo. Envie o arquivo na aba de upload.")
        return redirect("painel_video", pk=video.pk)
    return render(request, "panel/video_form.html", {"form": form, "video": video if pk else None})


def video_detalhe(request, pk):
    video = get_object_or_404(Video, pk=pk)
    progresses = (
        Progress.objects.filter(video=video)
        .select_related("enrollment", "enrollment__medico")
        .order_by("-last_seen_at")
    )
    return render(
        request,
        "panel/video_detalhe.html",
        {"video": video, "progresses": progresses},
    )


@require_POST
def video_reprocessar(request, pk):
    video = get_object_or_404(Video, pk=pk)
    if not video.original_path:
        messages.error(request, "Envie o arquivo antes de processar.")
        return redirect("painel_video", pk=pk)
    enqueue_transcode(video)
    record_audit(
        "video_reprocessado",
        actor=request.user,
        target=video.title,
        ip=request.META.get("REMOTE_ADDR"),
    )
    messages.success(request, "Vídeo reenfileirado para processamento.")
    return redirect("painel_video", pk=pk)


@require_POST
def video_remover(request, pk):
    video = get_object_or_404(Video, pk=pk)
    curso_id = video.course_id
    titulo = video.title
    video.delete()
    record_audit(
        "video_removido", actor=request.user, target=titulo, ip=request.META.get("REMOTE_ADDR")
    )
    messages.success(request, "Vídeo removido.")
    return redirect("painel_curso", pk=curso_id)


# --------------------------------------------------------------------------
# Médicos
# --------------------------------------------------------------------------
def medicos(request):
    busca = request.GET.get("q", "").strip()
    consulta = Medico.objects.select_related("user").all()
    if busca:
        consulta = consulta.filter(
            Q(nome__icontains=busca)
            | Q(cpf__icontains=busca.replace(".", "").replace("-", ""))
            | Q(crm__icontains=busca)
        )

    linhas = []
    for medico in consulta:
        matriculas = medico.enrollments.select_related("course")
        linhas.append(
            {
                "medico": medico,
                "matriculas": matriculas.count(),
                "aprovado": matriculas.filter(status=Enrollment.Status.APPROVED).count(),
                "ultimo": Progress.objects.filter(enrollment__medico=medico).aggregate(
                    last=Max("last_seen_at")
                )["last"],
            }
        )

    return render(request, "panel/medicos.html", {"linhas": linhas, "busca": busca})


def medico_form(request, pk=None):
    medico = get_object_or_404(Medico, pk=pk) if pk else None
    inicial = {"ativo": True}
    if medico:
        inicial["email"] = medico.email
    form = MedicoForm(request.POST or None, instance=medico, initial=inicial)

    senha_utilizada = None
    if request.method == "POST" and form.is_valid():
        medico = form.save()
        senha_utilizada = getattr(form, "senha_utilizada", None)
        if senha_utilizada:
            record_audit(
                "medico_criado",
                actor=request.user,
                target=medico.nome,
                detail={"cpf": medico.cpf, "senha_padrao": True},
                ip=request.META.get("REMOTE_ADDR"),
            )
            messages.success(
                request,
                f"Médico “{medico.nome}” cadastrado. Anote a senha inicial abaixo.",
            )
        else:
            record_audit(
                "medico_salvo",
                actor=request.user,
                target=medico.nome,
                detail={"cpf": medico.cpf},
                ip=request.META.get("REMOTE_ADDR"),
            )
            messages.success(request, f"Médico “{medico.nome}” salvo.")
            return redirect("painel_medicos")
        form = MedicoForm(instance=medico, initial={"email": medico.email})

    return render(
        request,
        "panel/medico_form.html",
        {"form": form, "medico": medico, "senha_utilizada": senha_utilizada},
    )


def medico_lote(request):
    form = MedicoLoteForm(request.POST or None)
    criados = []
    if request.method == "POST" and form.is_valid():
        criados = form.save()
        for item in criados:
            medico = item["medico"]
            record_audit(
                "medico_criado",
                actor=request.user,
                target=medico.nome,
                detail={"cpf": medico.cpf, "senha_padrao": True, "lote": True},
                ip=request.META.get("REMOTE_ADDR"),
            )
        messages.success(
            request,
            f"{len(criados)} médico(s) cadastrado(s) em lote. Anote as senhas iniciais.",
        )
        form = MedicoLoteForm()

    return render(request, "panel/medico_lote.html", {"form": form, "criados": criados})


@require_POST
def medico_acao(request, pk):
    medico = get_object_or_404(Medico, pk=pk)
    acao = request.POST.get("acao")

    if acao == "ativar":
        medico.ativo = True
        medico.save(update_fields=["ativo"])
        messages.success(request, "Médico ativado.")
    elif acao == "desativar":
        medico.ativo = False
        medico.save(update_fields=["ativo"])
        messages.success(request, "Médico desativado — o acesso ao portal é bloqueado.")
    elif acao == "resetar_senha":
        senha = redefinir_senha(medico)
        record_audit(
            "senha_resetada",
            actor=request.user,
            target=medico.nome,
            ip=request.META.get("REMOTE_ADDR"),
        )
        messages.success(
            request,
            f"Senha provisória de {medico.nome}: {senha} "
            "(ele será obrigado a trocá-la no próximo acesso).",
        )
    return redirect("painel_medicos")


# --------------------------------------------------------------------------
# Matrículas
# --------------------------------------------------------------------------
def matriculas(request):
    busca = request.GET.get("q", "").strip()
    situacao = request.GET.get("status", "").strip()
    curso_id = request.GET.get("curso", "").strip()

    consulta = Enrollment.objects.select_related("medico", "course").all()
    if busca:
        consulta = consulta.filter(
            Q(medico__nome__icontains=busca)
            | Q(medico__cpf__icontains=busca.replace(".", "").replace("-", ""))
        )
    if situacao:
        consulta = consulta.filter(status=situacao)
    if curso_id:
        consulta = consulta.filter(course_id=curso_id)

    linhas = []
    for matricula in consulta:
        cobertura = course_coverage(matricula)
        linhas.append(
            {
                "matricula": matricula,
                "cobertura": round(cobertura, 1),
                "exigido": float(matricula.min_percent or 0),
                "ultima": Progress.objects.filter(enrollment=matricula).aggregate(
                    last=Max("last_seen_at")
                )["last"],
            }
        )

    return render(
        request,
        "panel/matriculas.html",
        {
            "linhas": linhas,
            "busca": busca,
            "situacao": situacao,
            "curso_id": curso_id,
            "cursos": Course.objects.all(),
            "form": MatriculaForm(),
            "situacoes": Enrollment.Status.choices,
        },
    )


def matricula_form(request):
    form = MatriculaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        matricula = form.save()
        record_audit(
            "matricula_criada",
            actor=request.user,
            target=f"{matricula.medico.nome} — {matricula.course.title}",
            ip=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, "Matrícula registrada.")
        return redirect("painel_curso", pk=matricula.course_id)
    return render(request, "panel/matricula_form.html", {"form": form})


def _destino_matricula(request, curso_id):
    destino = request.POST.get("voltar")
    if destino and url_has_allowed_host_and_scheme(
        destino,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return destino
    return reverse("painel_curso", args=[curso_id])


@require_POST
def matricula_acao(request, pk):
    matricula = get_object_or_404(Enrollment, pk=pk)
    acao = request.POST.get("acao")
    curso_id = matricula.course_id

    if acao == "aprovar":
        matricula.status = Enrollment.Status.APPROVED
        matricula.approved_at = timezone.now()
        matricula.approved_by = request.user
        matricula.save(update_fields=["status", "approved_at", "approved_by"])
        record_audit(
            "aprovacao_manual",
            actor=request.user,
            target=matricula.medico.nome,
            detail={"curso": matricula.course.title},
            ip=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"{matricula.medico.nome} foi aprovado.")
    elif acao == "retirar_aprovacao":
        if matricula.status != Enrollment.Status.APPROVED:
            messages.error(request, "Esta matrícula não está aprovada.")
            return redirect(_destino_matricula(request, curso_id))
        matricula.status = Enrollment.Status.APPROVAL_REVOKED
        matricula.approved_at = None
        matricula.approved_by = None
        matricula.save(update_fields=["status", "approved_at", "approved_by"])
        record_audit(
            "aprovacao_retirada",
            actor=request.user,
            target=matricula.medico.nome,
            detail={"curso": matricula.course.title},
            ip=request.META.get("REMOTE_ADDR"),
        )
        messages.success(
            request,
            f"Aprovação de {matricula.medico.nome} retirada. O progresso foi preservado.",
        )
    elif acao == "reprovar":
        matricula.status = Enrollment.Status.REJECTED
        matricula.save(update_fields=["status"])
        messages.success(request, f"{matricula.medico.nome} foi marcado como reprovado.")
    elif acao == "resetar":
        reset_progress(matricula, actor=request.user)
        messages.success(request, f"Progresso de {matricula.medico.nome} resetado.")
    elif acao == "cancelar":
        matricula.status = Enrollment.Status.CANCELLED
        matricula.save(update_fields=["status"])
        messages.success(request, "Matrícula cancelada.")

    return redirect(_destino_matricula(request, curso_id))


def matricula_exportar(request, pk):
    curso = get_object_or_404(Course, pk=pk)
    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="turma-{curso.pk}.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "Matrícula",
            "CPF",
            "CRM",
            "Situação",
            "Cobertura %",
            "Exigido %",
            "Aprovado",
            "Tempo em aula",
            "Sessões",
            "Última atividade",
        ]
    )
    for matricula in curso.enrollments.select_related("medico").order_by("medico__nome"):
        progresses = Progress.objects.filter(enrollment=matricula)
        ultima = progresses.aggregate(last=Max("last_seen_at"))["last"]
        tempo = progresses.aggregate(total=Sum("watch_seconds"))["total"] or 0
        cobertura = course_coverage(matricula)
        writer.writerow(
            [
                matricula.medico.nome,
                matricula.medico.cpf,
                matricula.medico.crm,
                matricula.get_status_display(),
                f"{cobertura:.1f}".replace(".", ","),
                f"{float(matricula.min_percent or 0):.0f}",
                "sim" if cobertura >= float(matricula.min_percent or 0) else "não",
                format_hours(tempo),
                WatchSession.objects.filter(enrollment=matricula).count(),
                ultima.strftime("%d/%m/%Y %H:%M") if ultima else "",
            ]
        )
    return response


# --------------------------------------------------------------------------
# Auditoria
# --------------------------------------------------------------------------
def auditoria(request):
    logs = AuditLog.objects.select_related("actor").all()[:300]
    return render(request, "panel/auditoria.html", {"logs": logs})
