"""Serviços de mídia: assinatura de URLs, upload em blocos e processamento."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path

from django.conf import settings

from apps.courses.models import Video

QUALITY_PRESETS = [
    {"name": "720p", "height": 720, "bitrate": "2000k"},
    {"name": "540p", "height": 540, "bitrate": "1000k"},
    {"name": "360p", "height": 360, "bitrate": "500k"},
]

AUDIO_BITRATE = "96k"


# --------------------------------------------------------------------------
# Assinatura de URLs (HMAC)
# --------------------------------------------------------------------------
def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def sign_payload(payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(
        settings.SECRET_KEY.encode(), body, hashlib.sha256
    ).digest()
    return f"{_b64encode(body)}.{_b64encode(signature)}"


def verify_token(token: str) -> dict | None:
    """Valida assinatura e expiração. Devolve None se inválido."""
    if not token or "." not in token:
        return None
    body_b64, signature_b64 = token.split(".", 1)
    try:
        body = _b64decode(body_b64)
        signature = _b64decode(signature_b64)
    except Exception:  # noqa: BLE001
        return None

    expected = hmac.new(settings.SECRET_KEY.encode(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None

    if float(payload.get("exp", 0)) < time.time():
        return None
    return payload


def token_expiry() -> int:
    return int(time.time()) + settings.HLS_TOKEN_TTL_SECONDS


# --------------------------------------------------------------------------
# Playlists
# --------------------------------------------------------------------------
def parse_playlist(text: str) -> list[tuple[str, float]]:
    """Devolve [(nome_do_segmento, instante_inicial_em_segundos), ...]."""
    segments: list[tuple[str, float]] = []
    current = 0.0
    pending_duration = 0.0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            value = line.split(":", 1)[1].split(",")[0]
            try:
                pending_duration = float(value)
            except ValueError:
                pending_duration = 0.0
            continue
        if line.startswith("#"):
            continue
        segments.append((line.split("?")[0], current))
        current += pending_duration
        pending_duration = 0.0
    return segments


def duracao_hls(diretorio: Path) -> float:
    """Duração total (s) de uma pasta HLS, a partir dos EXTINF da variante."""
    for playlist in sorted(diretorio.glob("*/index.m3u8")):
        texto = playlist.read_text(encoding="utf-8")
        segmentos = parse_playlist(texto)
        if segmentos:
            return segmentos[-1][1] + 4.0
    return 0.0


def qualidades_hls(diretorio: Path) -> list[dict]:
    """Lê as variantes declaradas no master.m3u8."""
    master = diretorio / "master.m3u8"
    if not master.exists():
        return []
    linhas = master.read_text(encoding="utf-8").splitlines()
    qualidades: list[dict] = []
    for indice, linha in enumerate(linhas):
        if linha.startswith("#EXT-X-STREAM-INF:") and indice + 1 < len(linhas):
            atributos = linha.split(":", 1)[1]
            banda = 0
            resolucao = ""
            for parte in atributos.split(","):
                if parte.startswith("BANDWIDTH="):
                    banda = int(parte.split("=")[1] or 0)
                elif parte.startswith("RESOLUTION="):
                    resolucao = parte.split("=")[1]
            nome = linhas[indice + 1].strip().split("/")[0]
            qualidades.append(
                {"name": nome, "bandwidth": banda, "resolution": resolucao}
            )
    return qualidades


def build_signed_variant_playlist(
    enrollment_id: int, video_id: int, quality: str, text: str
) -> str:
    """Reescreve a playlist trocando os segmentos por URLs assinadas.

    As tags HLS (#EXTM3U, #EXT-X-TARGETDURATION, #EXTINF, …) são preservadas:
    sem elas o hls.js não consegue montar a linha do tempo do vídeo.
    """
    linhas: list[str] = []
    inicio = 0.0
    duracao_pendente = 0.0

    # A playlist VOD e carregada uma unica vez pelo hls.js. Os tokens dos
    # segmentos precisam valer ate o fim do video, mesmo que o TTL global
    # esteja curto; caso contrario a reproducao morre com 403 depois de
    # alguns minutos.
    duracao_total = 0.0
    for raw_line in text.splitlines():
        if raw_line.strip().startswith("#EXTINF:"):
            try:
                duracao_total += float(
                    raw_line.strip().split(":", 1)[1].split(",")[0]
                )
            except ValueError:
                pass
    expiracao = max(token_expiry(), int(time.time() + duracao_total + 600))

    for raw_line in text.splitlines():
        linha = raw_line.strip()
        if not linha:
            continue

        if linha.startswith("#EXTINF:"):
            try:
                duracao_pendente = float(linha.split(":", 1)[1].split(",")[0])
            except ValueError:
                duracao_pendente = 0.0
            linhas.append(linha)
            continue

        if linha.startswith("#"):
            linhas.append(linha)
            continue

        nome = linha.split("?")[0]
        token = sign_payload(
            {
                "v": video_id,
                "e": enrollment_id,
                "q": quality,
                "n": nome,
                "k": "segment",
                "s": round(inicio, 3),
                "exp": expiracao,
            }
        )
        linhas.append(f"/api/media/{video_id}/{quality}/{nome}?token={token}")
        inicio += duracao_pendente
        duracao_pendente = 0.0

    return "\n".join(linhas) + "\n"


def build_signed_master_playlist(
    enrollment_id: int, video_id: int, qualities: list[dict], text: str
) -> str:
    """Reescreve a playlist master trocando as variantes por URLs assinadas."""
    lines_out: list[str] = []
    bandwidths = {item["name"]: item.get("bandwidth") for item in qualities}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            lines_out.append(line)
            continue
        if line.startswith("#"):
            lines_out.append(line)
            continue
        quality = line.split("/")[0]
        token = sign_payload(
            {
                "v": video_id,
                "e": enrollment_id,
                "q": quality,
                "k": "variant",
                "b": bandwidths.get(quality, 0),
                "exp": token_expiry(),
            }
        )
        lines_out.append(
            f"/api/media/{video_id}/{quality}/index.m3u8?token={token}"
        )
    return "\n".join(lines_out) + "\n"


# --------------------------------------------------------------------------
# Upload em blocos
# --------------------------------------------------------------------------
def upload_dir(upload_id) -> Path:
    """Arquivo em montagem do upload (blocos gravados na posição final)."""
    return Path(settings.MEDIA_ROOT) / "tmp" / "uploads" / f"{upload_id}.mp4"


def original_path(video: Video) -> Path:
    return Path(settings.MEDIA_ROOT) / "orig" / f"{video.pk}.mp4"


def hls_dir(video: Video) -> Path:
    return Path(settings.MEDIA_ROOT) / "hls" / str(video.pk)


def hls_pending_dir(video: Video) -> Path:
    """HLS de uma versão ainda em processamento (troca atômica no fim)."""
    return Path(settings.MEDIA_ROOT) / "hls" / f"{video.pk}.pending"


def promote_hls(video: Video) -> None:
    """Promove o HLS pendente para a versão oficial.

    A versão antiga continua sendo servida até este ponto; a troca é feita
    só depois que o processamento termina com sucesso.
    """
    pendente = hls_pending_dir(video)
    if not pendente.exists():
        raise FileNotFoundError(f"HLS pendente não encontrado: {pendente}")

    oficial = hls_dir(video)
    if oficial.exists():
        shutil.rmtree(oficial)
    pendente.rename(oficial)


# --------------------------------------------------------------------------
# ffmpeg
# --------------------------------------------------------------------------
def run(command: list[str], timeout: int = 60 * 60 * 12) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=True
    )


def probe(source: Path) -> dict:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate:stream=codec_type,width,height,bit_rate,codec_name",
            "-of",
            "json",
            str(source),
        ],
        timeout=120,
    )
    return json.loads(result.stdout or "{}")


def video_stream_info(info: dict) -> dict:
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return {}


def probe_duration(source: Path) -> float:
    try:
        info = probe(source)
        return float(info.get("format", {}).get("duration") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def remux(source: Path, out_dir: Path, segment_seconds: int) -> None:
    """Recodifica apenas o container (rápido, mantém a qualidade original)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-c",
            "copy",
            "-hls_time",
            str(segment_seconds),
            "-hls_playlist_type",
            "vod",
            "-hls_flags",
            "independent_segments",
            "-start_number",
            "0",
            "-hls_segment_filename",
            str(out_dir / "seg_%05d.ts"),
            str(out_dir / "index.m3u8"),
        ]
    )


def transcode_variants(source: Path, out_root: Path, segment_seconds: int) -> list[dict]:
    """Gera 720p/540p/360p em uma única passagem de decodificação."""
    presets = [
        preset
        for preset in QUALITY_PRESETS
        if preset["height"] <= max(settings.VIDEO_MAX_HEIGHT, 360)
    ] or QUALITY_PRESETS[:1]
    count = len(presets)

    filters = [f"[0:v]split={count}" + "".join(f"[s{i}]" for i in range(count))]
    for index, preset in enumerate(presets):
        filters.append(f"[s{index}]scale=-2:{preset['height']}[v{index}]")
    filter_complex = ";".join(filters)

    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-filter_complex",
        filter_complex,
    ]

    for index, preset in enumerate(presets):
        target = out_root / preset["name"]
        target.mkdir(parents=True, exist_ok=True)
        command += [
            "-map",
            f"[v{index}]",
            "-map",
            "0:a:0?",
            f"-c:v:{index}",
            "libx264",
            f"-b:v:{index}",
            preset["bitrate"],
            f"-preset:v:{index}",
            "veryfast",
            f"-pix_fmt:v:{index}",
            "yuv420p",
            f"-force_key_frames:v:{index}",
            f"expr:gte(t,n_forced*{segment_seconds})",
            f"-c:a:{index}",
            "aac",
            f"-b:a:{index}",
            AUDIO_BITRATE,
            "-f",
            "hls",
            "-hls_time",
            str(segment_seconds),
            "-hls_playlist_type",
            "vod",
            "-hls_flags",
            "independent_segments",
            "-start_number",
            "0",
            "-hls_segment_filename",
            str(target / "seg_%05d.ts"),
            str(target / "index.m3u8"),
        ]

    run(command)
    return [
        {
            "name": preset["name"],
            "bandwidth": int(preset["bitrate"].rstrip("k")) * 1000,
            "resolution": f"{preset['height']}p",
        }
        for preset in presets
    ]


def make_poster(source: Path, destination: Path, duration: float) -> None:
    timestamp = max(0.0, (duration or 60.0) * 0.25)
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.2f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(destination),
        ],
        timeout=300,
    )


def write_master_playlist(out_root: Path, qualities: list[dict]) -> None:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for item in qualities:
        lines.append(
            f"#EXT-X-STREAM-INF:BANDWIDTH={int(item.get('bandwidth') or 800000)},"
            f"RESOLUTION={item.get('resolution') or '1280x720'}"
        )
        lines.append(f"{item['name']}/index.m3u8")
    (out_root / "master.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")


def enqueue_transcode(video: Video) -> None:
    from .models import Job

    Job.objects.filter(video=video, status__in=[Job.Status.QUEUED, Job.Status.RUNNING]).delete()
    Job.objects.create(kind=Job.Kind.TRANSCODE, video=video)
    video.status = Video.Status.QUEUED
    video.error_message = ""
    video.save(update_fields=["status", "error_message"])


def process_transcode(video: Video) -> dict:
    """Processa o arquivo original e gera o HLS. Devolve as qualidades.

    O HLS é gerado num diretório *pendente*; a troca pela versão oficial é
    feita por ``promote_hls`` só após o sucesso. Assim a versão anterior
    permanece disponível para os alunos durante o processamento.
    """
    source = original_path(video)
    if not source.exists():
        raise FileNotFoundError(f"Arquivo original não encontrado: {source}")

    out_root = hls_pending_dir(video)
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    segment_seconds = settings.HLS_SEGMENT_SECONDS
    duration = probe_duration(source)
    qualities: list[dict] = []

    info = probe(source)
    stream = video_stream_info(info)
    altura = int(stream.get("height") or 720)

    modo = settings.VIDEO_PIPELINE
    if modo == "auto":
        modo = "transcode" if altura > settings.VIDEO_MAX_HEIGHT else "remux"

    if modo == "remux":
        nome = f"{altura}p"
        remux(source, out_root / nome, segment_seconds)
        bandwidth = int(stream.get("bit_rate") or 0)
        if not bandwidth:
            total = int(info.get("format", {}).get("bit_rate") or 0)
            bandwidth = total or 2_000_000
        width = int(stream.get("width") or 1280)
        qualities = [
            {
                "name": nome,
                "bandwidth": bandwidth,
                "resolution": f"{width}x{altura}",
            }
        ]
    else:
        qualities = transcode_variants(source, out_root, segment_seconds)

    make_poster(source, out_root / "poster.jpg", duration)
    write_master_playlist(out_root, qualities)

    return {"duration": duration, "qualities": qualities, "modo": modo}


def chunk_count(total_size: int, chunk_size: int) -> int:
    return max(1, math.ceil(total_size / chunk_size))


def limpar_uploads_abandonados(max_horas: int = 48) -> int:
    """Remove os arquivos temporários de sessões de upload abandonadas.

    Uma sessão é considerada abandonada quando não é atualizada há mais de
    ``max_horas`` — normalmente porque o navegador foi fechado no meio do envio.
    """
    from datetime import timedelta

    from django.utils import timezone

    from .models import UploadSession

    limite = timezone.now() - timedelta(hours=max_horas)
    removidos = 0
    for sessao in UploadSession.objects.filter(updated_at__lt=limite).exclude(
        status=UploadSession.Status.COMPLETED
    ):
        # Se o vídeo já está pronto, o upload pendente é obsoleto.
        if sessao.video.status == Video.Status.READY:
            caminho = upload_dir(sessao.pk)
            if caminho.exists():
                caminho.unlink(missing_ok=True)
            sessao.delete()
            removidos += 1
    return removidos


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def free_disk_space(path: Path) -> int:
    usage = shutil.disk_usage(path)
    return usage.free


def safe_filename(name: str) -> str:
    return os.path.basename(name or "video.mp4").replace("..", "_")
