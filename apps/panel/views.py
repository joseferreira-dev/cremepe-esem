"""Views do painel administrativo."""

from __future__ import annotations

import io
import re
import secrets
from datetime import datetime

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max, Q, Sum, TextField
from django.db.models.functions import Cast
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.models import Medico, normalize_cpf
from apps.accounts.services import criar_conta_medico, redefinir_senha
from apps.courses.models import AuditLog, Course, Enrollment, Progress, Video, WatchSession
from apps.courses.services import (
    course_coverage,
    criterios_de_aprovacao,
    format_hours,
    posicionar_video,
    record_audit,
    renumerar_videos,
    reset_progress,
)
from apps.media.services import enqueue_transcode
from apps.web.templatetags.web import cor_status

from .decorators import somente_admin, somente_superuser
from .forms import (
    AdministradorForm,
    CursoForm,
    MatriculaForm,
    MedicoForm,
    MedicoLoteForm,
    VideoForm,
)


# --------------------------------------------------------------------------
# Visão geral
# --------------------------------------------------------------------------
def _faixa_desempenho(matriculas) -> list[dict]:
    """Agrupa matrículas por faixa de cobertura assistida."""
    faixas = [
        {"rotulo": "0–25%", "total": 0, "cor": "bg-slate-300"},
        {"rotulo": "25–50%", "total": 0, "cor": "bg-sky-400"},
        {"rotulo": "50–75%", "total": 0, "cor": "bg-pacifico-500"},
        {"rotulo": "75–100%", "total": 0, "cor": "bg-marca-500"},
    ]
    for matricula in matriculas:
        cobertura = course_coverage(matricula)
        if cobertura < 25:
            faixas[0]["total"] += 1
        elif cobertura < 50:
            faixas[1]["total"] += 1
        elif cobertura < 75:
            faixas[2]["total"] += 1
        else:
            faixas[3]["total"] += 1
    return faixas


def index(request):
    cursos = list(Course.objects.all())
    matriculas = Enrollment.objects.select_related("medico", "course")

    total_medicos = Medico.objects.filter(ativo=True).count()
    ativas = matriculas.exclude(status=Enrollment.Status.REJECTED)
    aprovadas = matriculas.filter(status=Enrollment.Status.APPROVED).count()

    # Ranking por cobertura; só os 5 mais adiantados são exibidos — e a última
    # atividade (uma consulta por matrícula) é buscada apenas deles.
    coberturas = [
        (matricula, course_coverage(matricula))
        for matricula in ativas.select_related("medico", "course")
    ]
    coberturas.sort(key=lambda par: par[1], reverse=True)

    em_andamento = []
    for matricula, cobertura in coberturas[:5]:
        em_andamento.append(
            {
                "matricula": matricula,
                "cobertura": round(cobertura, 1),
                "ultima": Progress.objects.filter(enrollment=matricula)
                .aggregate(last=Max("last_seen_at"))["last"],
            }
        )

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
            "linhas": linhas,
            "videos_processando": videos_processando,
            "videos_com_erro": videos_com_erro,
            "ultimos_logs": AuditLog.objects.select_related("actor")[:5],
            "sessoes_hoje": WatchSession.objects.filter(
                started_at__date=timezone.now().date()
            ).count(),
        },
    )


# --------------------------------------------------------------------------
# Cursos
# --------------------------------------------------------------------------
PAGINA_TAMANHO = 50
ALUNOS_POR_PAGINA = 10  # listagem de alunos (médicos): 10 por página


def _paginar(request, consulta, tamanho=PAGINA_TAMANHO):
    """Página da listagem (valores inválidos são absorvidos).

    Paginar ANTES de montar as linhas também evita montar dezenas de milhares
    de linhas (e suas consultas por linha) quando só uma página é exibida.
    """
    return Paginator(consulta, tamanho).get_page(request.GET.get("pagina"))


def cursos(request):
    busca = request.GET.get("q", "").strip()
    situacao = request.GET.get("status", "").strip()
    consulta = Course.objects.all()
    if busca:
        consulta = consulta.filter(Q(title__icontains=busca))
    if situacao:
        consulta = consulta.filter(status=situacao)

    pagina = _paginar(request, consulta)
    linhas = []
    for curso in pagina:
        matriculas = curso.enrollments.select_related("medico")
        linhas.append(
            {
                "curso": curso,
                "total": matriculas.count(),
                "aprovados": matriculas.filter(status=Enrollment.Status.APPROVED).count(),
                "videos": curso.video_count,
                "duracao": format_hours(curso.total_duration),
            }
        )

    return render(
        request,
        "panel/cursos.html",
        {
            "linhas": linhas,
            "busca": busca,
            "situacao": situacao,
            "situacoes": Course.Status.choices,
            "pagina": pagina,
        },
    )


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


def _extrair_cpfs(texto):
    """Separa CPFs de um texto (aceita pontos/traços; por linha ou vírgula).

    Devolve (válidos únicos na ordem do texto, inválidos).
    """
    normalizado = (texto or "").replace(".", "").replace("-", "")
    encontrados = [token for token in re.split(r"[^0-9]+", normalizado) if token]
    validos = []
    invalidos = []
    for token in encontrados:
        if len(token) == 11:
            if token not in validos:
                validos.append(token)
        else:
            invalidos.append(token)
    return validos, invalidos


def _cpf_formatado(cpf: str) -> str:
    if len(cpf) != 11:
        return cpf
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def curso_detalhe(request, pk):
    curso = get_object_or_404(Course, pk=pk)
    videos = list(curso.videos.all())

    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "matricular_cpf":
            cpf = normalize_cpf(request.POST.get("cpf", ""))
            if len(cpf) != 11:
                messages.error(request, "Informe um CPF com 11 dígitos.")
            else:
                medico = Medico.objects.filter(cpf=cpf).first()
                if medico is None:
                    messages.warning(
                        request,
                        f"CPF {_cpf_formatado(cpf)} não está cadastrado. "
                        "Cadastre em Painel → Médicos → Cadastro em lote.",
                    )
                elif not medico.ativo:
                    messages.error(
                        request,
                        f"{medico.nome} está inativo — o acesso ao portal está bloqueado.",
                    )
                else:
                    matricula, criada = Enrollment.objects.get_or_create(
                        medico=medico, course=curso
                    )
                    if criada:
                        record_audit(
                            "matricula_criada",
                            actor=request.user,
                            target=f"{matricula.medico.nome} — {curso.title}",
                            ip=request.META.get("REMOTE_ADDR"),
                        )
                        messages.success(
                            request,
                            f"{matricula.medico.nome} matriculado(a) no curso.",
                        )
                    else:
                        messages.warning(
                            request,
                            f"{matricula.medico.nome} já está matriculado(a) "
                            "neste curso.",
                        )
            return redirect("painel_curso", pk=curso.pk)

        if acao == "matricular_lote_cpfs":
            cpfs, invalidos = _extrair_cpfs(request.POST.get("cpfs", ""))
            if not cpfs and not invalidos:
                messages.error(request, "Cole ao menos um CPF para matricular.")
                return redirect("painel_curso", pk=curso.pk)

            medicos = {
                medico.cpf: medico for medico in Medico.objects.filter(cpf__in=cpfs)
            }
            ja_matriculados = set(
                Enrollment.objects.filter(course=curso, medico__cpf__in=cpfs)
                .values_list("medico__cpf", flat=True)
            )
            criadas = 0
            falhas = []  # [exibição do CPF, motivo da não matrícula]
            for cpf in cpfs:
                medico = medicos.get(cpf)
                if medico is None:
                    falhas.append(
                        [_cpf_formatado(cpf), "Não encontrado no cadastro"]
                    )
                elif not medico.ativo:
                    falhas.append([_cpf_formatado(cpf), "Aluno inativo"])
                elif cpf in ja_matriculados:
                    falhas.append(
                        [_cpf_formatado(cpf), "Já matriculado neste curso"]
                    )
                else:
                    matricula, criada = Enrollment.objects.get_or_create(
                        medico=medico, course=curso
                    )
                    if criada:
                        criadas += 1
                        record_audit(
                            "matricula_criada",
                            actor=request.user,
                            target=f"{matricula.medico.nome} — {curso.title}",
                            detail={"lote": True},
                            ip=request.META.get("REMOTE_ADDR"),
                        )
            for token in invalidos:
                falhas.append([token, "CPF inválido (não tem 11 dígitos)"])

            # Relatório exibido uma única vez na próxima carga da página.
            request.session["relatorio_matricula"] = {
                "criadas": criadas,
                "falhas": falhas,
            }
            return redirect("painel_curso", pk=curso.pk)

    linhas = []
    turma = _paginar(
        request,
        curso.enrollments.select_related("medico").order_by("medico__nome"),
    )
    for matricula in turma:
        progresses = Progress.objects.filter(enrollment=matricula)
        criterio = criterios_de_aprovacao(matricula)
        linhas.append(
            {
                "matricula": matricula,
                "cobertura": round(criterio["cobertura"], 1),
                "exigido": criterio["exigido"],
                "pendencias": criterio["pendencias"],
                "aprovado": matricula.status == Enrollment.Status.APPROVED,
                "ultima": progresses.aggregate(last=Max("last_seen_at"))["last"],
                "tempo": format_hours(progresses.aggregate(total=Sum("watch_seconds"))["total"] or 0),
                "sessoes": WatchSession.objects.filter(enrollment=matricula).count(),
            }
        )

    ativas = curso.enrollments.exclude(status=Enrollment.Status.REJECTED)
    faixa_desempenho = _faixa_desempenho(ativas)
    total_ativas = ativas.count()

    # Relatório do último lote (exibido uma vez; some no reload seguinte).
    relatorio = request.session.pop("relatorio_matricula", None)

    return render(
        request,
        "panel/curso_detalhe.html",
        {
            "curso": curso,
            "videos": videos,
            "linhas": linhas,
            "pagina": turma,
            "relatorio": relatorio,
            "duracao": format_hours(curso.total_duration),
            "faixa_desempenho": faixa_desempenho,
            "total_ativas": total_ativas,
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
    form = VideoForm(request.POST or None, instance=video)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            posicao = int(form.cleaned_data["order"])
            video_salvo = form.save()
            # Inclusão ou edição: garante a posição pedida e deixa a
            # sequência do curso compactada (1..N).
            posicionar_video(video_salvo, posicao)
            if curso_anterior_id and curso_anterior_id != video_salvo.course_id:
                # Mudou de curso: a sequência de origem ficou com um buraco.
                renumerar_videos(curso_anterior_id)

        video = video_salvo
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
    with transaction.atomic():
        video.delete()
        # A exclusão deixa um buraco: renumera o restante do curso para 1..N.
        renumerar_videos(curso_id)
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
    situacao = request.GET.get("situacao", "").strip()
    consulta = Medico.objects.select_related("user").all()
    if busca:
        consulta = consulta.filter(
            Q(nome__icontains=busca)
            | Q(cpf__icontains=busca.replace(".", "").replace("-", ""))
            | Q(crm__icontains=busca)
        )
    if situacao == "ativo":
        consulta = consulta.filter(ativo=True)
    elif situacao == "inativo":
        consulta = consulta.filter(ativo=False)
    elif situacao == "provisoria":
        consulta = consulta.filter(must_change_password=True)

    pagina = _paginar(request, consulta, tamanho=ALUNOS_POR_PAGINA)
    linhas = []
    for medico in pagina:
        linhas.append(
            {
                "medico": medico,
                "ultimo": Progress.objects.filter(enrollment__medico=medico).aggregate(
                    last=Max("last_seen_at")
                )["last"],
            }
        )

    return render(
        request,
        "panel/medicos.html",
        {
            "linhas": linhas,
            "busca": busca,
            "situacao": situacao,
            "pagina": pagina,
        },
    )


def medico_detalhe(request, pk):
    """Dados do aluno e de suas matrículas (conteúdo do diálogo da listagem)."""
    medico = get_object_or_404(Medico.objects.select_related("user"), pk=pk)

    matriculas = []
    for matricula in medico.enrollments.select_related("course"):
        ultima = Progress.objects.filter(enrollment=matricula).aggregate(
            last=Max("last_seen_at")
        )["last"]
        matriculas.append(
            {
                "matricula": matricula,
                "cobertura": round(course_coverage(matricula), 1),
                "ultima": ultima,
            }
        )

    ultimo_geral = Progress.objects.filter(enrollment__medico=medico).aggregate(
        last=Max("last_seen_at")
    )["last"]
    return render(
        request,
        "panel/medico_detalhe.html",
        {"medico": medico, "matriculas": matriculas, "ultimo": ultimo_geral},
    )


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


# Tamanho do pedaço do cadastro em lote: ~15 alunos por requisição (o hash
# de senha custa ~200-300 ms cada) — cada chamada fica bem abaixo do timeout
# do nginx (60 s) e do gunicorn (180 s), e o JS acompanha a barra de
# progresso enquanto os pedaços acontecem.
LOTE_TAMANHO = 15


def medico_lote(request):
    form = MedicoLoteForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        # A validação completa é rápida; a gravação acontece em pedaços
        # (medico_lote_parcial) — lotes grandes estouravam o timeout e
        # perdiam tudo (transação abortada).
        request.session["lote_alunos"] = form.linhas
        request.session["lote_inicio"] = 0
        form = MedicoLoteForm()
        return render(
            request,
            "panel/medico_lote.html",
            {
                "form": form,
                "lote_iniciar": True,
                "lote_total": len(request.session["lote_alunos"]),
            },
        )
    return render(request, "panel/medico_lote.html", {"form": form})


@require_POST
def medico_lote_parcial(request):
    """Cria o próximo pedaço do lote guardado na sessão (chamado pelo JS).

    O deslocamento vive na sessão (não no cliente): recarregar a página
    retoma de onde parou e o envio duplicado não cria alunos repetidos —
    CPFs já cadastrados são ignorados e contados.
    """
    linhas = request.session.get("lote_alunos") or []
    inicio = request.session.get("lote_inicio") or 0
    fatia = linhas[inicio : inicio + LOTE_TAMANHO]

    criados = []
    ignorados = 0
    with transaction.atomic():
        for dados in fatia:
            if Medico.objects.filter(cpf=dados["cpf"]).exists():
                ignorados += 1
                record_audit(
                    "medico_duplicado_ignorado",
                    actor=request.user,
                    target=dados["nome"],
                    detail={"cpf": dados["cpf"], "lote": True},
                    ip=request.META.get("REMOTE_ADDR"),
                )
                continue
            medico = Medico(ativo=True, **dados)
            senha = criar_conta_medico(medico)
            medico.save()
            record_audit(
                "medico_criado",
                actor=request.user,
                target=medico.nome,
                detail={"cpf": medico.cpf, "senha_padrao": True, "lote": True},
                ip=request.META.get("REMOTE_ADDR"),
            )
            criados.append(
                {"nome": medico.nome, "cpf": medico.cpf, "senha": senha}
            )

    processados = min(inicio + len(fatia), len(linhas))
    if processados >= len(linhas):
        request.session.pop("lote_alunos", None)
        request.session.pop("lote_inicio", None)
    else:
        request.session["lote_inicio"] = processados
    return JsonResponse(
        {
            "processados": processados,
            "total": len(linhas),
            "criados": criados,
            "ignorados": ignorados,
        }
    )


@require_POST
def medico_acao(request, pk):
    medico = get_object_or_404(Medico, pk=pk)
    acao = request.POST.get("acao")

    if acao == "ativar":
        medico.ativo = True
        medico.save(update_fields=["ativo"])
        record_audit(
            "medico_ativado",
            actor=request.user,
            target=medico.nome,
            detail={"cpf": medico.cpf},
            ip=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, "Médico ativado.")
    elif acao == "desativar":
        medico.ativo = False
        medico.save(update_fields=["ativo"])
        record_audit(
            "medico_desativado",
            actor=request.user,
            target=medico.nome,
            detail={"cpf": medico.cpf},
            ip=request.META.get("REMOTE_ADDR"),
        )
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

    consulta = Enrollment.objects.select_related("medico", "course").all()
    if busca:
        consulta = consulta.filter(
            Q(medico__nome__icontains=busca)
            | Q(medico__cpf__icontains=busca.replace(".", "").replace("-", ""))
        )
    if situacao:
        consulta = consulta.filter(status=situacao)

    pagina = _paginar(request, consulta)
    linhas = []
    for matricula in pagina:
        cobertura = course_coverage(matricula)
        linhas.append(
            {
                "matricula": matricula,
                "cobertura": round(cobertura, 1),
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
            "pagina": pagina,
            "busca": busca,
            "situacao": situacao,
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
        return redirect("painel_matriculas")
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
    mensagem = ""

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
        mensagem = f"{matricula.medico.nome} foi aprovado."
    elif acao == "reprovar":
        matricula.status = Enrollment.Status.REJECTED
        matricula.approved_at = None
        matricula.approved_by = None
        matricula.save(update_fields=["status", "approved_at", "approved_by"])
        record_audit(
            "reprovacao_manual",
            actor=request.user,
            target=matricula.medico.nome,
            detail={"curso": matricula.course.title},
            ip=request.META.get("REMOTE_ADDR"),
        )
        mensagem = f"{matricula.medico.nome} foi marcado como reprovado."
    elif acao == "resetar":
        reset_progress(matricula, actor=request.user)
        mensagem = f"Progresso de {matricula.medico.nome} resetado."
    elif acao == "remover":
        nome = matricula.medico.nome
        curso = matricula.course.title
        record_audit(
            "matricula_removida",
            actor=request.user,
            target=f"{nome} — {curso}",
            ip=request.META.get("REMOTE_ADDR"),
        )
        matricula.delete()
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "removido": True})
        messages.success(request, "Matrícula removida.")
        return redirect(_destino_matricula(request, curso_id))

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse(
            {
                "ok": True,
                "status": matricula.status,
                "status_display": matricula.get_status_display(),
                "status_class": cor_status(matricula.status),
                "mensagem": mensagem,
            }
        )

    if mensagem:
        messages.success(request, mensagem)
    return redirect(_destino_matricula(request, curso_id))


def matricula_exportar(request, pk):
    """Planilha XLSX da turma, formatada (título, cores, filtros e larguras)."""
    curso = get_object_or_404(Course, pk=pk)

    linhas = []
    for matricula in curso.enrollments.select_related("medico").order_by("medico__nome"):
        progresses = Progress.objects.filter(enrollment=matricula)
        ultima = progresses.aggregate(last=Max("last_seen_at"))["last"]
        tempo = progresses.aggregate(total=Sum("watch_seconds"))["total"] or 0
        criterio = criterios_de_aprovacao(matricula)
        pendencias = " | ".join(
            f"{item['video'].title}: {item['atual']:.1f}% (min. {item['exigido']:.0f}%)"
            for item in criterio["pendencias"]
        )
        linhas.append(
            {
                "nome": matricula.medico.nome,
                "cpf": matricula.medico.cpf,
                "crm": matricula.medico.crm or "",
                "situacao": matricula.get_status_display(),
                "status": matricula.status,
                "cobertura": round(criterio["cobertura"], 1),
                "exigido": float(criterio["exigido"]),
                "atende": criterio["atende"],
                "pendencias": pendencias,
                "tempo": format_hours(tempo),
                "sessoes": WatchSession.objects.filter(enrollment=matricula).count(),
                "ultima": ultima,
            }
        )

    cabecalhos = [
        "Matrícula",
        "CPF",
        "CRM",
        "Situação",
        "Cobertura %",
        "Exigido %",
        "Aprovado",
        "Aulas abaixo do mínimo",
        "Tempo em aula",
        "Sessões",
        "Última atividade",
    ]
    larguras = [32, 14, 12, 16, 13, 12, 11, 45, 13, 9, 18]

    # Paleta do portal.
    verde_escuro = "006448"  # marca-700
    borda_cor = "E2E8F0"  # slate-200
    cores_situacao = {
        "approved": ("EDFBF6", "006448"),  # marca-50 / marca-700
        "in_progress": ("E0F2FE", "075985"),  # sky-100 / sky-700
        "enrolled": ("F1F5F9", "334155"),  # slate-100 / slate-700
        "rejected": ("FFE4E6", "BE123C"),  # rose-100 / rose-700
        "cancelled": ("F1F5F9", "94A3B8"),  # slate-100 / slate-400
    }

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Turma"
    borda = Border(
        left=Side(style="thin", color=borda_cor),
        right=Side(style="thin", color=borda_cor),
        top=Side(style="thin", color=borda_cor),
        bottom=Side(style="thin", color=borda_cor),
    )

    # Linha 1: título do curso.
    ws.merge_cells("A1:K1")
    titulo = ws["A1"]
    titulo.value = curso.title
    titulo.font = Font(bold=True, size=14, color="FFFFFF")
    titulo.fill = PatternFill("solid", fgColor=verde_escuro)
    titulo.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[1].height = 28

    # Linha 2: contexto da exportação.
    ws.merge_cells("A2:K2")
    contexto = ws["A2"]
    exportado = timezone.localtime().strftime("%d/%m/%Y às %H:%M")
    contexto.value = (
        f"Exportado em {exportado} · {len(linhas)} matrícula(s) · "
        f"mínimo {float(curso.min_percent):g}% para aprovação"
    )
    contexto.font = Font(size=9, italic=True, color="64748B")
    contexto.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[2].height = 16

    # Linha 3: cabeçalho (congelado e com filtro).
    for indice, texto in enumerate(cabecalhos, start=1):
        celula = ws.cell(row=3, column=indice, value=texto)
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor=verde_escuro)
        celula.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        celula.border = borda
    ws.row_dimensions[3].height = 30
    ws.freeze_panes = "A4"

    # Dados.
    for indice, linha in enumerate(linhas, start=4):
        valores = [
            linha["nome"],
            linha["cpf"],
            linha["crm"],
            linha["situacao"],
            linha["cobertura"],
            linha["exigido"],
            "Sim" if linha["atende"] else "Não",
            linha["pendencias"],
            linha["tempo"],
            linha["sessoes"],
            linha["ultima"],
        ]
        fundo_situacao, texto_situacao = cores_situacao.get(
            linha["status"], ("F1F5F9", "334155")
        )
        for coluna, valor in enumerate(valores, start=1):
            # O Excel não aceita datetime consciente do fuso (USE_TZ) —
            # openpyxl levanta TypeError no save. Converte para horário
            # local sem tzinfo.
            if isinstance(valor, datetime) and valor.tzinfo is not None:
                valor = timezone.localtime(valor).replace(tzinfo=None)
            celula = ws.cell(row=indice, column=coluna, value=valor)
            celula.border = borda
            celula.alignment = Alignment(vertical="center", wrap_text=(coluna == 8))
            if coluna in (2, 3, 4, 5, 6, 7, 9, 10, 11):
                celula.alignment = Alignment(
                    horizontal="center", vertical="center", wrap_text=(coluna == 8)
                )
        # Destaque da situação.
        celula_situacao = ws.cell(row=indice, column=4)
        celula_situacao.fill = PatternFill("solid", fgColor=fundo_situacao)
        celula_situacao.font = Font(bold=True, color=texto_situacao)
        # Percentuais com formato de percentual literal (o valor é 0–100).
        ws.cell(row=indice, column=5).number_format = '0.0"%"'
        ws.cell(row=indice, column=6).number_format = '0"%"'
        # Aprovado em verde/vermelho.
        celula_aprovado = ws.cell(row=indice, column=7)
        celula_aprovado.font = Font(
            bold=True, color="006448" if linha["atende"] else "BE123C"
        )
        # Última atividade como data/hora real do Excel.
        celula_ultima = ws.cell(row=indice, column=11)
        if linha["ultima"] is None:
            celula_ultima.value = "—"
        else:
            celula_ultima.number_format = "dd/mm/yyyy hh:mm"

    for indice, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(indice)].width = largura
    if linhas:
        ws.auto_filter.ref = f"A3:K{3 + len(linhas)}"

    buffer = io.BytesIO()
    wb.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    response["Content-Disposition"] = f'attachment; filename="turma-{curso.pk}.xlsx"'
    return response


# --------------------------------------------------------------------------
# Auditoria
# --------------------------------------------------------------------------
# Opções de ordenação da auditoria: valor do select -> chaves de order_by.
ORDENACOES_AUDITORIA = (
    ("recentes", "Mais recentes"),
    ("antigas", "Mais antigas"),
    ("acao", "Ação (A–Z)"),
    ("alvo", "Objeto (A–Z)"),
    ("ator", "Autor (A–Z)"),
)
_CHAVES_ORDEM_AUDITORIA = {
    "recentes": ("-created_at",),
    "antigas": ("created_at",),
    "acao": ("action", "-created_at"),
    "alvo": ("target", "-created_at"),
    "ator": ("actor__username", "-created_at"),
}


def auditoria(request):
    busca = request.GET.get("q", "").strip()
    acao = request.GET.get("acao", "").strip()
    ator = request.GET.get("ator", "").strip()
    ordenacao = request.GET.get("ordenacao", "").strip() or "recentes"

    logs = AuditLog.objects.select_related("actor")
    if busca:
        # O detalhe é JSON: buscamos como texto para varrer chaves e valores.
        logs = logs.annotate(detalhe_txt=Cast("detail", TextField())).filter(
            Q(action__icontains=busca)
            | Q(target__icontains=busca)
            | Q(actor__username__icontains=busca)
            | Q(detalhe_txt__icontains=busca)
        )
    if acao:
        logs = logs.filter(action=acao)
    if ator == "sistema":
        logs = logs.filter(actor__isnull=True)
    elif ator:
        logs = logs.filter(actor__username=ator)
    logs = logs.order_by(*_CHAVES_ORDEM_AUDITORIA.get(ordenacao, ("-created_at",)))

    pagina = _paginar(request, logs)
    # order_by() limpa a ordenação do Meta antes do distinct (exigência do
    # SELECT DISTINCT do PostgreSQL).
    acoes = sorted(
        AuditLog.objects.order_by().values_list("action", flat=True).distinct()
    )
    atores = sorted(
        User.objects.filter(audit_logs__isnull=False)
        .order_by()
        .values_list("username", flat=True)
        .distinct()
    )
    return render(
        request,
        "panel/auditoria.html",
        {
            "logs": pagina,
            "pagina": pagina,
            "busca": busca,
            "acao": acao,
            "ator": ator,
            "ordenacao": ordenacao,
            "ordenacoes": ORDENACOES_AUDITORIA,
            "acoes": acoes,
            "atores": atores,
            "tem_filtros": bool(
                busca or acao or ator or ordenacao != "recentes"
            ),
        },
    )


# --------------------------------------------------------------------------
# Usuários administradores do painel
# --------------------------------------------------------------------------
@somente_superuser
def administradores(request):
    busca = request.GET.get("q", "").strip()
    consulta = User.objects.filter(is_staff=True, is_superuser=False).order_by(
        "first_name", "last_name", "username"
    )
    if busca:
        consulta = consulta.filter(
            Q(username__icontains=busca)
            | Q(first_name__icontains=busca)
            | Q(last_name__icontains=busca)
            | Q(email__icontains=busca)
        )
    pagina = _paginar(request, consulta)
    return render(
        request,
        "panel/administradores.html",
        {"linhas": list(pagina), "busca": busca, "pagina": pagina},
    )


@somente_superuser
def administrador_form(request, pk=None):
    usuario = get_object_or_404(User, pk=pk) if pk else None
    if usuario and usuario.is_superuser:
        messages.error(request, "Superusuários são gerenciados pelo suporte de TI.")
        return redirect("painel_administradores")

    form = AdministradorForm(request.POST or None, instance=usuario)
    if request.method == "POST" and form.is_valid():
        novo = usuario is None
        usuario = form.save()
        record_audit(
            "administrador_criado" if novo else "administrador_salvo",
            actor=request.user,
            target=usuario.username,
            detail={"email": usuario.email, "ativo": usuario.is_active},
            ip=request.META.get("REMOTE_ADDR"),
        )
        messages.success(
            request,
            f"Usuário “{usuario.username}” {'criado' if novo else 'salvo'}.",
        )
        return redirect("painel_administradores")

    return render(
        request,
        "panel/administrador_form.html",
        {"form": form, "usuario": usuario},
    )


@somente_superuser
@require_POST
def administrador_acao(request, pk):
    usuario = get_object_or_404(User, pk=pk, is_staff=True, is_superuser=False)
    acao = request.POST.get("acao")

    if acao == "ativar":
        usuario.is_active = True
        usuario.save(update_fields=["is_active"])
        record_audit(
            "administrador_ativado",
            actor=request.user,
            target=usuario.username,
            ip=request.META.get("REMOTE_ADDR"),
        )
        messages.success(request, f"Usuário “{usuario.username}” ativado.")
    elif acao == "desativar":
        usuario.is_active = False
        usuario.save(update_fields=["is_active"])
        record_audit(
            "administrador_desativado",
            actor=request.user,
            target=usuario.username,
            ip=request.META.get("REMOTE_ADDR"),
        )
        messages.success(
            request,
            f"Usuário “{usuario.username}” desativado — o acesso ao painel foi bloqueado.",
        )
    elif acao == "resetar_senha":
        senha = secrets.token_urlsafe(9)
        usuario.set_password(senha)
        usuario.save(update_fields=["password"])
        record_audit(
            "administrador_senha_resetada",
            actor=request.user,
            target=usuario.username,
            ip=request.META.get("REMOTE_ADDR"),
        )
        messages.success(
            request,
            f"Senha provisória de {usuario.username}: {senha}",
        )

    return redirect("painel_administradores")
