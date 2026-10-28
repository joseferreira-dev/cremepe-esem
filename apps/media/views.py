"""Endpoints de upload, player e entrega do vídeo."""

from __future__ import annotations

import hashlib
import json
import logging
from functools import wraps

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.views import redirect_to_login
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.tokens import gerar_token_api, token_do_request, validar_token_api
from apps.courses.models import Enrollment, Progress, Video, WatchSession
from apps.courses.services import (
    apply_heartbeat,
    course_coverage,
    course_videos,
    is_video_unlocked,
    record_audit,
    refresh_enrollment_status,
    unlocked_videos,
)

from .models import UploadSession
from .services import (
    build_signed_master_playlist,
    build_signed_variant_playlist,
    chunk_count,
    enqueue_transcode,
    hls_dir,
    original_path,
    safe_filename,
    sign_payload,
    upload_dir,
    verify_token,
)

logger = logging.getLogger(__name__)


def upload_staff_required(view):
    """Como staff_member_required, mas responde JSON em vez de redirecionar."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse(
                {
                    "error": (
                        "Sessão expirada. Recarregue a página de upload e "
                        "entre novamente."
                    )
                },
                status=401,
            )
        if not request.user.is_staff:
            return JsonResponse(
                {"error": "Acesso restrito à administração do portal."},
                status=403,
            )
        return view(request, *args, **kwargs)

    return wrapper


def upload_ajax_required(view):
    """Protege os endpoints de upload contra POSTs de formulário externos.

    O upload usa um cabeçalho próprio enviado pelo ``fetch``; formulários
    cross-site não conseguem defini-lo e um ``fetch`` cross-origin exigiria
    preflight CORS, que não é permitido. Isso permite dispensar o CSRF por
    cookie nesses endpoints sem abrir a ação para outros sites.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if (
            request.headers.get("X-Requested-With") != "XMLHttpRequest"
            and not request.GET.get("api_token")
        ):
            return JsonResponse({"error": "Requisição inválida."}, status=403)
        return view(request, *args, **kwargs)

    return wrapper


def api_login_required(view):
    """Exige sessao ativa.

    Para requisicoes do player (XHR) devolve 401 em JSON — em vez de redirecionar
    para a tela de login — para que o JavaScript consiga tratar a situacao
    (por exemplo, sessao expirada) sem tentar ler HTML como JSON.

    Um 401 **nunca** apaga o cookie de sessao. Uma aba antiga (com cookie
    orfao) pode disparar o player depois de outro login bem-sucedido; se a
    resposta apagasse o cookie, ela derrubaria a sessao nova e valida. O
    navegador converge para a sessao correta na proxima navegacao.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                token = token_do_request(request)
                token_valido = validar_token_api(token)
                token_exp = None
                if token:
                    try:
                        token_exp = int(token.split(".")[2])
                    except (IndexError, ValueError):
                        pass
                autorizacao = request.headers.get("Authorization", "")
                auth_token = (
                    autorizacao[7:].strip()
                    if autorizacao.lower().startswith("bearer ")
                    else ""
                )
                auth_exp = None
                if auth_token:
                    try:
                        auth_exp = int(auth_token.split(".")[2])
                    except (IndexError, ValueError):
                        pass
                query_token = request.GET.get("api_token", "").strip()
                query_exp = None
                if query_token:
                    try:
                        query_exp = int(query_token.split(".")[2])
                    except (IndexError, ValueError):
                        pass
                logger.warning(
                    "API sem sessao valida: path=%s cookie_bruto=%r cookies=%r "
                    "session_key=%r session_no_banco=%s meta_csrf=%r token=%s "
                    "token_len=%s token_prefix=%r token_exp=%r token_valido=%r "
                    "auth_prefix=%r auth_exp=%r auth_valido=%r "
                    "query_prefix=%r query_exp=%r query_valido=%r agora=%s",
                    request.path,
                    request.META.get("HTTP_COOKIE", ""),
                    dict(request.COOKIES),
                    request.session.session_key,
                    Session.objects.filter(
                        session_key=request.session.session_key or ""
                    ).exists(),
                    request.META.get("CSRF_COOKIE"),
                    "presente" if token else "ausente",
                    len(token),
                    token[:24],
                    token_exp,
                    token_valido,
                    auth_token[:24],
                    auth_exp,
                    bool(validar_token_api(auth_token)),
                    query_token[:24],
                    query_exp,
                    bool(validar_token_api(query_token)),
                    int(timezone.now().timestamp()),
                )
                return JsonResponse(
                    {"error": "Sess\u00e3o expirada. Entre novamente no portal."},
                    status=401,
                )
            return redirect_to_login(request.get_full_path())
        return view(request, *args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------
# Upload em blocos (admin)
# --------------------------------------------------------------------------
@require_GET
@never_cache
@staff_member_required
def upload_page(request):
    videos = (
        Video.objects.select_related("course")
        .order_by("course__title", "order")
    )
    placeholder = "00000000-0000-0000-0000-000000000000"
    return render(
        request,
        "panel/upload.html",
        {
            "videos": videos,
            "upload_urls": {
                "init": reverse("upload_init"),
                "chunk": reverse("upload_chunk", kwargs={"upload_id": placeholder}),
                "complete": reverse("upload_complete", kwargs={"upload_id": placeholder}),
                "placeholder": placeholder,
                # Alguns ambientes não enviam o cookie de sessão nos fetch do
                # navegador; o token assinado mantém o upload autenticado.
                "api_token": gerar_token_api(
                    request.user,
                    request.session.session_key,
                    validade=max(settings.API_TOKEN_TTL_SECONDS, 12 * 3600),
                ),
            },
        },
    )


@require_POST
@csrf_exempt
@upload_staff_required
@upload_ajax_required
def upload_init(request):
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido."}, status=400)

    video = get_object_or_404(Video, pk=data.get("video_id"))
    filename = safe_filename(data.get("filename") or "video.mp4")
    try:
        total_size = int(data.get("size") or 0)
    except (TypeError, ValueError):
        total_size = 0

    if total_size <= 0:
        return JsonResponse({"error": "Tamanho do arquivo inválido."}, status=400)

    substituicao = data.get("substituicao") or Video.Substituicao.NENHUMA
    if substituicao not in Video.Substituicao.values:
        return JsonResponse({"error": "Tipo de substituição inválido."}, status=400)
    # Um vídeo que ainda não tem mídia nunca é substituição.
    if not video.tem_media:
        substituicao = Video.Substituicao.NENHUMA

    chunk_size = int(settings.UPLOAD_CHUNK_SIZE)

    # Retomada: se já existe um envio incompleto do mesmo vídeo com o mesmo
    # tamanho, reaproveitamos a sessão (os blocos já gravados são preservados).
    existente = (
        UploadSession.objects.filter(
            video=video,
            total_size=total_size,
            status=UploadSession.Status.UPLOADING,
        )
        .order_by("-updated_at")
        .first()
    )
    if existente is not None:
        if existente.substituicao != substituicao:
            existente.substituicao = substituicao
            existente.save(update_fields=["substituicao"])
        return JsonResponse(
            {
                "upload_id": str(existente.pk),
                "chunk_size": existente.chunk_size,
                "total_chunks": existente.total_chunks,
                "received": existente.received,
                "retomado": True,
            }
        )

    session = UploadSession.objects.create(
        video=video,
        original_filename=filename,
        substituicao=substituicao,
        total_size=total_size,
        chunk_size=chunk_size,
        total_chunks=chunk_count(total_size, chunk_size),
        status=UploadSession.Status.UPLOADING,
    )

    video.original_filename = filename
    video.status = Video.Status.UPLOADING
    video.save(update_fields=["original_filename", "status"])

    record_audit(
        "upload_iniciado",
        actor=request.user,
        target=f"Vídeo #{video.pk}",
        detail={"arquivo": filename, "bytes": total_size},
        ip=client_ip(request),
    )

    return JsonResponse(
        {
            "upload_id": str(session.pk),
            "chunk_size": session.chunk_size,
            "total_chunks": session.total_chunks,
            "received": session.received,
        }
    )


@require_POST
@csrf_exempt
@upload_staff_required
@upload_ajax_required
def upload_chunk(request, upload_id):
    session = get_object_or_404(UploadSession, pk=upload_id)
    try:
        index = int(request.headers.get("X-Chunk-Index", "-1"))
    except (TypeError, ValueError):
        index = -1

    if index < 0 or index >= session.total_chunks:
        return JsonResponse({"error": "Índice de bloco inválido."}, status=400)

    payload = request.read()
    expected = (
        session.chunk_size
        if index < session.total_chunks - 1
        else session.total_size - session.chunk_size * (session.total_chunks - 1)
    )
    if len(payload) != expected:
        return JsonResponse(
            {"error": f"Bloco com tamanho inesperado ({len(payload)} != {expected})."},
            status=400,
        )

    checksum = request.headers.get("X-Chunk-SHA256")
    if checksum and hashlib.sha256(payload).hexdigest().lower() != checksum.lower():
        return JsonResponse({"error": "Checksum do bloco divergente."}, status=400)

    # O bloco é gravado direto na posição final do arquivo: evita a etapa de
    # merge (que dobraria o uso de disco e o consumo de memória).
    destino = upload_dir(session.pk)
    destino.parent.mkdir(parents=True, exist_ok=True)
    modo = "r+b" if destino.exists() else "w+b"
    with open(destino, modo) as arquivo:
        arquivo.seek(index * session.chunk_size)
        arquivo.write(payload)

    received = sorted(set(list(session.received or []) + [index]))
    session.received = received
    session.status = UploadSession.Status.UPLOADING
    session.save(update_fields=["received", "status", "updated_at"])

    return JsonResponse(
        {
            "received": len(received),
            "total_chunks": session.total_chunks,
            "index": index,
        }
    )


@require_POST
@csrf_exempt
@upload_staff_required
@upload_ajax_required
def upload_complete(request, upload_id):
    session = get_object_or_404(UploadSession, pk=upload_id)
    video = session.video

    missing = [
        index for index in range(session.total_chunks) if index not in (session.received or [])
    ]
    if missing:
        return JsonResponse(
            {"error": f"Faltam {len(missing)} bloco(s).", "missing": missing[:50]},
            status=400,
        )

    source = upload_dir(session.pk)
    if not source.exists() or source.stat().st_size != session.total_size:
        tamanho = source.stat().st_size if source.exists() else 0
        return JsonResponse(
            {
                "error": f"Arquivo incompleto ({tamanho} de {session.total_size} bytes). "
                "Execute o envio novamente para retomar."
            },
            status=400,
        )

    destination = original_path(video)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)

    video.original_path = str(destination.relative_to(settings.MEDIA_ROOT))
    video.substituicao = session.substituicao
    video.status = Video.Status.QUEUED
    video.save(update_fields=["original_path", "status", "substituicao"])

    session.status = UploadSession.Status.COMPLETED
    session.save(update_fields=["status", "updated_at"])

    enqueue_transcode(video)

    record_audit(
        "upload_concluido",
        actor=request.user,
        target=f"Vídeo #{video.pk}",
        detail={
            "arquivo": session.original_filename,
            "bytes": session.total_size,
            "substituicao": session.substituicao,
        },
        ip=client_ip(request),
    )

    return JsonResponse({"ok": True, "video_id": video.pk, "status": video.status})


@require_GET
@staff_member_required
def upload_status(request, upload_id):
    session = get_object_or_404(UploadSession, pk=upload_id)
    return JsonResponse(
        {
            "upload_id": str(session.pk),
            "received": session.received,
            "total_chunks": session.total_chunks,
            "status": session.status,
        }
    )


# --------------------------------------------------------------------------
# Player
# --------------------------------------------------------------------------
def _get_enrollment(request, enrollment_id) -> Enrollment:
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("course", "medico"), pk=enrollment_id
    )
    if enrollment.medico.user_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied
    return enrollment


@require_GET
@api_login_required
@csrf_exempt
def player_state(request):
    enrollment = _get_enrollment(request, request.GET.get("enrollment"))
    video = get_object_or_404(Video, pk=request.GET.get("video"))

    if enrollment.course_id != video.course_id:
        raise PermissionDenied

    progress, _ = Progress.objects.get_or_create(
        enrollment=enrollment, video=video, defaults={"intervals": []}
    )

    unlocked = [item.id for item in unlocked_videos(enrollment)]
    master_token = sign_payload(
        {
            "v": video.pk,
            "e": enrollment.pk,
            "k": "master",
            "exp": int(timezone.now().timestamp()) + settings.HLS_TOKEN_TTL_SECONDS,
        }
    )

    max_allowed = (
        (progress.furthest or 0)
        + settings.SEGMENT_TOLERANCE_SECONDS
        + settings.HEARTBEAT_INTERVAL_SECONDS
    )
    # A retomada nunca passa do trecho ja assistido. A tolerancia serve para
    # liberar segmentos a frente, nao para iniciar a reproducao neles.
    limite_retomada = progress.furthest or 0
    state = {
        "video_id": video.pk,
        "video_title": video.title,
        "enrollment_id": enrollment.pk,
        "course_id": enrollment.course_id,
        "duration": video.duration or 0,
        # Nunca devolver uma posicao de retomada alem do trecho liberado.
        "last_position": min(progress.last_position or 0, limite_retomada),
        "furthest": progress.furthest or 0,
        "max_allowed": max_allowed,
        "covered": progress.covered or 0,
        "video_percent": round(
            ((progress.covered or 0) / video.duration * 100)
            if progress and video.duration
            else 0.0,
            1,
        ),
        "course_percent": round(course_coverage(enrollment), 1),
        "required_percent": float(enrollment.min_percent or 0),
        "unlocked": video.pk in unlocked,
        "unlocked_videos": unlocked,
        "heartbeat_interval": settings.HEARTBEAT_INTERVAL_SECONDS,
        "manifest_url": (
            f"/api/media/{video.pk}/master.m3u8?token={master_token}"
            if video.pk in unlocked
            else ""
        ),
        "poster_url": (
            f"/api/media/{video.pk}/poster.jpg" if video.poster else ""
        ),
    }
    return JsonResponse(state)


@require_POST
@api_login_required
@csrf_exempt
def heartbeat(request):
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido."}, status=400)

    enrollment = _get_enrollment(request, data.get("enrollment"))
    video = get_object_or_404(Video, pk=data.get("video"))

    if enrollment.course_id != video.course_id:
        raise PermissionDenied
    if not is_video_unlocked(enrollment, video):
        return JsonResponse({"error": "Vídeo ainda não liberado."}, status=403)

    result = apply_heartbeat(
        enrollment,
        video,
        position=data.get("position", 0),
        playing=bool(data.get("playing")),
    )
    progress = result["progress"]

    _touch_session(request, enrollment, video)
    refresh_enrollment_status(enrollment)

    # Aula seguinte agora liberada? O player usa isto para atualizar a lista
    # lateral na hora, sem exigir reload quando um vídeo termina.
    proxima_liberada = False
    if progress.completed_at is not None:
        videos = course_videos(enrollment.course)
        for indice, candidato in enumerate(videos):
            if candidato.pk == video.pk:
                if indice + 1 < len(videos):
                    proxima_liberada = is_video_unlocked(
                        enrollment, videos[indice + 1]
                    )
                break

    payload = {
        "authorized_position": round(result["authorized_position"], 3),
        "furthest": round(progress.furthest or 0, 3),
        "max_allowed": round(result["max_allowed"], 3),
        "covered": round(progress.covered or 0, 3),
        "video_percent": round(
            ((progress.covered or 0) / video.duration * 100)
            if video.duration
            else 0.0,
            1,
        ),
        "course_percent": round(course_coverage(enrollment), 1),
        "suspicious": result["suspicious"],
        "enrollment_status": enrollment.status,
        "completed": progress.completed_at is not None,
        "next_unlocked": proxima_liberada,
    }
    return JsonResponse(payload)


def _touch_session(request, enrollment: Enrollment, video: Video) -> WatchSession:
    now = timezone.now()
    session = (
        WatchSession.objects.filter(enrollment=enrollment, video=video)
        .order_by("-started_at")
        .first()
    )
    if session is None or (now - session.started_at).total_seconds() > 120:
        session = WatchSession.objects.create(
            enrollment=enrollment,
            video=video,
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:300],
            device=request.headers.get("X-Device", "")[:120],
        )
    session.ended_at = now
    session.save(update_fields=["ended_at"])
    return session


# --------------------------------------------------------------------------
# Playlists e segmentos
# --------------------------------------------------------------------------
def _authorize(request, payload, video_id: int, kind: str) -> Enrollment | None:
    if not payload or payload.get("k") != kind:
        return None
    if int(payload.get("v", 0)) != int(video_id):
        return None

    enrollment = (
        Enrollment.objects.select_related("course", "medico")
        .filter(pk=payload.get("e"))
        .first()
    )
    if enrollment is None:
        return None
    # O token assinado ja e a credencial do recurso. Se houver usuario
    # autenticado, ainda assim ele precisa ser o dono (ou staff); sem sessao,
    # o token de curta duracao (emitido so apos autenticar o master) basta.
    if request.user.is_authenticated:
        if enrollment.medico.user_id != request.user.id and not request.user.is_staff:
            return None
    if enrollment.status in {
        Enrollment.Status.CANCELLED,
        Enrollment.Status.REJECTED,
    }:
        return None
    return enrollment


@require_GET
@api_login_required
@csrf_exempt
def master_manifest(request, video_id):
    payload = verify_token(request.GET.get("token", ""))
    enrollment = _authorize(request, payload, video_id, "master")
    if enrollment is None:
        return HttpResponseForbidden("Token inválido ou expirado.")

    video = get_object_or_404(Video, pk=video_id)
    if not is_video_unlocked(enrollment, video):
        return HttpResponseForbidden("Vídeo ainda não liberado.")

    master = hls_dir(video) / "master.m3u8"
    if not master.exists():
        return HttpResponseForbidden("Mídia indisponível.")

    text = master.read_text(encoding="utf-8")
    content = build_signed_master_playlist(
        enrollment.pk, video.pk, video.qualities or [], text
    )
    response = HttpResponse(content, content_type="application/vnd.apple.mpegurl")
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
@csrf_exempt
def variant_manifest(request, video_id, quality):
    """Playlist assinada: o token e a credencial, sem depender de cookie/header."""
    payload = verify_token(request.GET.get("token", ""))
    enrollment = _authorize(request, payload, video_id, "variant")
    if enrollment is None or payload.get("q") != quality:
        return HttpResponseForbidden("Token inválido ou expirado.")

    video = get_object_or_404(Video, pk=video_id)
    if not is_video_unlocked(enrollment, video):
        return HttpResponseForbidden("Vídeo ainda não liberado.")

    playlist = hls_dir(video) / quality / "index.m3u8"
    if not playlist.exists():
        return HttpResponseForbidden("Mídia indisponível.")

    text = playlist.read_text(encoding="utf-8")
    content = build_signed_variant_playlist(enrollment.pk, video.pk, quality, text)
    response = HttpResponse(content, content_type="application/vnd.apple.mpegurl")
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
@csrf_exempt
def segment(request, video_id, quality, segment_name):
    """Segmento assinado: funciona mesmo se o proxy reescrever headers."""
    payload = verify_token(request.GET.get("token", ""))
    enrollment = _authorize(request, payload, video_id, "segment")
    if enrollment is None:
        return HttpResponseForbidden("Token inválido ou expirado.")
    if payload.get("q") != quality or payload.get("n") != segment_name:
        return HttpResponseForbidden("Token não corresponde ao recurso.")

    video = get_object_or_404(Video, pk=video_id)
    if not is_video_unlocked(enrollment, video):
        return HttpResponseForbidden("Vídeo ainda não liberado.")

    progress = Progress.objects.filter(enrollment=enrollment, video=video).first()
    furthest = progress.furthest if progress else 0.0
    segment_start = float(payload.get("s") or 0.0)
    limit = (
        furthest
        + settings.SEGMENT_TOLERANCE_SECONDS
        + settings.HEARTBEAT_INTERVAL_SECONDS
    )

    if segment_start > limit:
        return HttpResponseForbidden("Avanço além do ponto liberado.")

    response = HttpResponse()
    response["X-Accel-Redirect"] = (
        f"/protected-media/hls/{video.pk}/{quality}/{segment_name}"
    )
    response["Content-Type"] = "video/mp2t"
    response["Cache-Control"] = "private, max-age=60"
    return response


@require_GET
@api_login_required
@csrf_exempt
def poster(request, video_id):
    video = get_object_or_404(Video, pk=video_id)
    path = hls_dir(video) / "poster.jpg"
    if not path.exists():
        return HttpResponseForbidden("Thumb indisponível.")
    response = HttpResponse()
    response["X-Accel-Redirect"] = f"/protected-media/hls/{video.pk}/poster.jpg"
    response["Content-Type"] = "image/jpeg"
    response["Cache-Control"] = "private, max-age=300"
    return response


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
