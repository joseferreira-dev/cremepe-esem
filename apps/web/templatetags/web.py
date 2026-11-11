from django import template

import os

register = template.Library()


@register.simple_tag(takes_context=True)
def paginas(context, numero):
    """URL da página pedida, preservando os filtros atuais da listagem."""
    request = context.get("request")
    if request is None:
        return f"?pagina={numero}"
    params = request.GET.copy()
    params["pagina"] = numero
    return f"?{params.urlencode()}"


@register.filter
def nome_arquivo(caminho):
    """Só o nome do arquivo, sem as pastas (``modelos/x.docx`` -> ``x.docx``)."""
    return os.path.basename(str(caminho or ""))


@register.filter
def primeiro_e_ultimo(nome):
    """'João Pereira da Silva' -> 'João Silva' (para o ícone do menu)."""
    partes = str(nome or "").split()
    if len(partes) <= 2:
        return " ".join(partes)
    return f"{partes[0]} {partes[-1]}"


@register.filter
def duracao(seconds):
    """Formata segundos como ``3h53min`` / ``45min`` / ``30s``."""
    try:
        total = int(float(seconds or 0))
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return "—"
    horas, resto = divmod(total, 3600)
    minutos, segundos = divmod(resto, 60)
    if horas:
        return f"{horas}h{minutos:02d}min"
    if minutos:
        return f"{minutos}min"
    return f"{segundos}s"


@register.filter
def posicao(seconds):
    """Segundos como posição do player: ``7:31`` / ``1:03:12``.

    Diferente de ``duracao`` (que resume em minutos), preserva os segundos —
    é o formato do ponto de retomada exibido no CTA "Continuar".
    """
    try:
        total = int(float(seconds or 0))
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return "0:00"
    horas, resto = divmod(total, 3600)
    minutos, segundos = divmod(resto, 60)
    if horas:
        return f"{horas}:{minutos:02d}:{segundos:02d}"
    return f"{minutos}:{segundos:02d}"


@register.filter
def porcento(valor, casas=1):
    try:
        return f"{float(valor or 0):.{casas}f}".replace(".", ",") + "%"
    except (TypeError, ValueError):
        return "0%"


@register.filter
def tamanho_arquivo(bytes_):
    try:
        total = float(bytes_ or 0)
    except (TypeError, ValueError):
        return "—"
    unidades = ["B", "KB", "MB", "GB", "TB"]
    indice = 0
    while total >= 1024 and indice < len(unidades) - 1:
        total /= 1024
        indice += 1
    return f"{total:.1f} {unidades[indice]}".replace(".", ",")


@register.filter
def data_hora(valor):
    if not valor:
        return "—"
    return valor.strftime("%d/%m/%Y às %H:%M")


@register.filter
def data_curta(valor):
    if not valor:
        return "—"
    return valor.strftime("%d/%m/%Y")


@register.filter
def cor_status(status):
    """Classes do badge conforme a situação da matrícula/vídeo."""
    mapa = {
        "approved": "bg-marca-100 text-marca-700 ring-1 ring-marca-200",
        "in_progress": "bg-pacifico-50 text-pacifico-700 ring-1 ring-pacifico-200",
        "enrolled": "bg-slate-100 text-slate-700 ring-1 ring-slate-200",
        "rejected": "bg-rose-50 text-rose-700 ring-1 ring-rose-200",
        "ready": "bg-marca-100 text-marca-700 ring-1 ring-marca-200",
        "processing": "bg-amber-50 text-amber-700 ring-1 ring-amber-200",
        "queued": "bg-amber-50 text-amber-700 ring-1 ring-amber-200",
        "uploading": "bg-sky-50 text-sky-700 ring-1 ring-sky-200",
        "error": "bg-rose-50 text-rose-700 ring-1 ring-rose-200",
        "empty": "bg-slate-100 text-slate-600 ring-1 ring-slate-200",
        "active": "bg-marca-100 text-marca-700 ring-1 ring-marca-200",
        "draft": "bg-slate-100 text-slate-600 ring-1 ring-slate-200",
        "archived": "bg-slate-100 text-slate-500 ring-1 ring-slate-200",
    }
    return mapa.get(str(status), "bg-slate-100 text-slate-700 ring-1 ring-slate-200")
