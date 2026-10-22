"""Teste de fumaça (ponta a ponta) do portal.

Executa contra uma instância em execução:

    python scripts/smoke_test.py
    BASE_URL=http://servidor:8080 MEDICO_CPF=12345678901 MEDICO_SENHA=xxx \
        MATRICULA_ID=2 VIDEO_ID=5 python scripts/smoke_test.py

Verifica: login por CPF, estado do player, heartbeats, bloqueio de avanço
(cliente e servidor) e entrega do segmento.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from _env import exigir

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
CPF = exigir("MEDICO_CPF")
SENHA = exigir("MEDICO_SENHA")
MATRICULA = os.environ.get("MATRICULA_ID", "1")
VIDEO = os.environ.get("VIDEO_ID", "1")

FALHAS: list[str] = []


def verificar(condicao: bool, descricao: str, detalhe: str = "") -> None:
    if condicao:
        print(f"  [ok]   {descricao}")
    else:
        print(f"  [FALHA] {descricao} {detalhe}")
        FALHAS.append(descricao)


class SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Cliente:
    def __init__(self, seguir_redirecionamento: bool = True) -> None:
        self.jar = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(self.jar)]
        if not seguir_redirecionamento:
            handlers.append(SemRedirecionar())
        self.opener = urllib.request.build_opener(*handlers)

    def pedir(self, caminho: str, dados: dict | None = None, metodo: str = "GET", form: bool = False):
        url = f"{BASE_URL}{caminho}"
        corpo = None
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": self.csrf_cookie(),
        }
        if dados is not None:
            if form:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                corpo = urllib.parse.urlencode(dados).encode()
            else:
                headers["Content-Type"] = "application/json"
                corpo = json.dumps(dados).encode()
        request = urllib.request.Request(url, data=corpo, headers=headers, method=metodo)
        try:
            with self.opener.open(request) as resposta:
                return resposta.status, resposta.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as erro:
            return erro.code, erro.read().decode("utf-8", "replace")

    def csrf(self, caminho: str = "/entrar/") -> str:
        _, html = self.pedir(caminho)
        achado = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
        return achado.group(1) if achado else ""

    def csrf_cookie(self) -> str:
        for cookie in self.jar:
            if cookie.name == "csrftoken":
                return cookie.value
        return ""


def main() -> int:
    print(f"Testando {BASE_URL} (matrícula {MATRICULA}, vídeo {VIDEO})\n")
    cliente = Cliente()

    print("1) Login por CPF")
    token = cliente.csrf()
    status, _ = cliente.pedir(
        "/entrar/",
        {
            "csrfmiddlewaretoken": token,
            "cpf": CPF,
            "senha": SENHA,
        },
        metodo="POST",
        form=True,
    )
    verificar(status == 200, f"login do médico (HTTP {status})")
    _, html = cliente.pedir("/")
    verificar("Meus cursos" in html, "área do aluno carregou")

    print("\n2) Estado do player")
    status, corpo = cliente.pedir(
        f"/api/player/state/?enrollment={MATRICULA}&video={VIDEO}"
    )
    verificar(status == 200, f"player_state (HTTP {status})")
    estado = json.loads(corpo)
    print(
        f"       duração={estado['duration']}s "
        f"({estado['duration'] / 3600:.1f}h) furthest={estado['furthest']}s"
    )
    verificar("manifest_url" in estado, "manifest_url presente")

    print("\n3) Heartbeat e bloqueio de avanço")
    cliente.pedir(
        "/api/player/heartbeat/",
        {
            "enrollment": int(MATRICULA),
            "video": int(VIDEO),
            "position": 0,
            "playing": False,
        },
        metodo="POST",
    )
    print("       aguardando 12s para simular reprodução real...")
    time.sleep(12)
    _, corpo = cliente.pedir(
        "/api/player/heartbeat/",
        {
            "enrollment": int(MATRICULA),
            "video": int(VIDEO),
            "position": 12,
            "playing": True,
        },
        metodo="POST",
    )
    dados = json.loads(corpo)
    verificar(
        abs(dados["authorized_position"] - 12) < 1.5,
        "posição de 12s é creditada (reprodução real)",
        f"-> {dados['authorized_position']}",
    )

    _, corpo = cliente.pedir(
        "/api/player/heartbeat/",
        {
            "enrollment": int(MATRICULA),
            "video": int(VIDEO),
            "position": 600,
            "playing": True,
        },
        metodo="POST",
    )
    dados = json.loads(corpo)
    verificar(
        dados["authorized_position"] < 30,
        "salto de 10 minutos é recusado pelo servidor",
        f"-> autorizado em {dados['authorized_position']}s",
    )
    verificar(dados["suspicious"] is True, "evento marcado como suspeito")

    print("\n4) Playlists assinadas")
    token_mestre = estado["manifest_url"].split("token=")[1]
    _, corpo = cliente.pedir(f"/api/media/{VIDEO}/master.m3u8?token={token_mestre}")
    variantes = [linha for linha in corpo.splitlines() if "index.m3u8" in linha]
    verificar(len(variantes) > 0, f"master.m3u8 lista as variantes ({len(variantes)})")
    token_variante = variantes[0].split("token=")[1]
    # /api/media/<id>/<qualidade>/index.m3u8
    qualidade = urllib.parse.urlparse(variantes[0]).path.split("/")[4]

    _, corpo = cliente.pedir(
        f"/api/media/{VIDEO}/{qualidade}/index.m3u8?token={token_variante}"
    )
    segmentos = [linha for linha in corpo.splitlines() if linha.startswith("/api/media/")]
    verificar(len(segmentos) > 0, f"playlist de variante assinada ({len(segmentos)} segmentos)")

    print("\n5) Entrega dos segmentos")
    primeiro = segmentos[0]
    caminho = up_parse(primeiro)
    status, _ = cliente.pedir(caminho)
    verificar(status == 200, f"primeiro segmento liberado (HTTP {status})")

    ultimo = segmentos[-1]
    caminho = up_parse(ultimo)
    status, _ = cliente.pedir(caminho)
    verificar(status == 403, f"segmento além do limite é bloqueado (HTTP {status})")

    print("\n6) Autorização")
    anonimo = Cliente(seguir_redirecionamento=False)
    status, _ = anonimo.pedir(
        f"/api/player/state/?enrollment={MATRICULA}&video={VIDEO}"
    )
    verificar(status == 302, f"usuário anônimo é redirecionado (HTTP {status})")

    status, _ = anonimo.pedir(f"/api/media/{VIDEO}/master.m3u8?token=invalido")
    verificar(status in (302, 403), f"token inválido é recusado (HTTP {status})")

    print("\n7) Painel administrativo")
    status, _ = anonimo.pedir("/painel/")
    verificar(status in (302, 403), f"painel exige login (HTTP {status})")

    print()
    if FALHAS:
        print(f"FALHAS: {len(FALHAS)}")
        for falha in FALHAS:
            print(f"  - {falha}")
        return 1
    print("Todos os testes passaram.")
    return 0


def up_parse(url: str) -> str:
    partes = urllib.parse.urlparse(url)
    return partes.path + "?" + partes.query


if __name__ == "__main__":
    sys.exit(main())
