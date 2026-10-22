"""Confere se todos os assets referenciados pela página são servidos corretamente.

Roda o fluxo de login (com troca de senha, se exigida), carrega a página do
curso e verifica cada URL de CSS/JS: status, tamanho e cache.

    python scripts/conferir_assets.py
"""

from __future__ import annotations

import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from _env import exigir

BASE = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
CPF = exigir("MEDICO_CPF")
SENHA = exigir("MEDICO_SENHA")
SENHA_NOVA = exigir("MEDICO_SENHA_NOVA")

FALHAS: list[str] = []


def verificar(condicao: bool, descricao: str, detalhe: str = "") -> None:
    print(f"  [{'ok' if condicao else 'FALHA'}] {descricao} {detalhe}")
    if not condicao:
        FALHAS.append(descricao)


jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def pedir(caminho: str, metodo: str = "GET", dados=None):
    corpo = None
    headers = {}
    if dados is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        corpo = urllib.parse.urlencode(dados).encode()
    request = urllib.request.Request(BASE + caminho, data=corpo, headers=headers, method=metodo)
    try:
        with opener.open(request) as resposta:
            return resposta.status, resposta.geturl(), dict(resposta.headers), resposta.read().decode(
                "utf-8", "replace"
            )
    except urllib.error.HTTPError as erro:
        return erro.code, caminho, dict(erro.headers), erro.read().decode("utf-8", "replace")


def token(html: str) -> str:
    achado = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    return achado.group(1) if achado else ""


status, url, _, html = pedir("/entrar/")
status, url, _, _ = pedir(
    "/entrar/",
    metodo="POST",
    dados={"csrfmiddlewaretoken": token(html), "cpf": CPF, "senha": SENHA},
)

if "/entrar/" in url:
    print(f"  FALHA no login com a senha '{SENHA}' (o médico já trocou a senha?).")
    print("  Use: python scripts/conferir_assets.py  (após resetar a senha padrão)")
    sys.exit(1)

if "/trocar-senha/" in url:
    _, _, _, html = pedir("/trocar-senha/")
    _, url, _, _ = pedir(
        "/trocar-senha/",
        metodo="POST",
        dados={
            "csrfmiddlewaretoken": token(html),
            "old_password": SENHA,
            "new_password1": SENHA_NOVA,
            "new_password2": SENHA_NOVA,
        },
    )
    print(f"  (senha trocada para {SENHA_NOVA})")

status, url, _, html = pedir("/cursos/1/")
print(f"  (página obtida: {url.replace(BASE, '')})")
verificar(status == 200 and "player" in html, "página do curso carregada", f"(HTTP {status})")

print("\nAssets referenciados:")
urls = sorted(set(re.findall(r'(?:src|href)="(/static/[^"]+)"', html)))
for caminho in urls:
    status, _, cabecalhos, corpo = pedir(caminho)
    tamanho = len(corpo)
    verificar(
        status == 200 and tamanho > 0,
        f"{caminho}",
        f"(HTTP {status}, {tamanho} bytes, cache={cabecalhos.get('Cache-Control', '-')})",
    )

print("\nEstrutura da página:")
tem_player = "/static/js/player." in html
verificar(tem_player, "player referenciado (com hash no nome)")
verificar("hls.min" in html, "hls.js referenciado")
verificar(
    "window.CREMEPE" in html
    and html.index("window.CREMEPE") < html.index("/static/js/player."),
    "configuração do player carregada ANTES do player.js",
)
verificar('data-unlocked="1"' in html, "existe vídeo liberado na lista")
