"""Simula a jornada do médico no primeiro acesso (validação do cenário).

    python scripts/jornada_primeiro_acesso.py

Verifica: login com a senha inicial padrão, exigência de troca de senha,
acesso ao curso, permissão de reprodução dos vídeos e estado zerado do progresso.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from _env import exigir

BASE = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
CPF = exigir("MEDICO_CPF")
SENHA_INICIAL = exigir("MEDICO_SENHA_INICIAL")
SENHA_NOVA = exigir("MEDICO_SENHA_NOVA")
NOME = os.environ.get("MEDICO_NOME", "").strip()

FALHAS: list[str] = []


def verificar(condicao: bool, descricao: str, detalhe: str = "") -> None:
    print(f"  [{'ok' if condicao else 'FALHA'}] {descricao} {detalhe}")
    if not condicao:
        FALHAS.append(descricao)


class Navegador:
    """Cliente HTTP com cookies, sem seguir redirecionamentos."""

    def __init__(self, seguir: bool = True):
        self.jar = http.cookiejar.CookieJar()
        manipuladores = [urllib.request.HTTPCookieProcessor(self.jar)]
        if not seguir:
            manipuladores.append(SemRedirecionar())
        self.opener = urllib.request.build_opener(*manipuladores)

    def get(self, caminho: str):
        return self._abrir(caminho)

    def post(self, caminho: str, dados: dict, form: bool = True):
        headers = {"X-Requested-With": "XMLHttpRequest", "X-CSRFToken": self.csrf()}
        if form:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            corpo = urllib.parse.urlencode(dados).encode()
        else:
            headers["Content-Type"] = "application/json"
            corpo = json.dumps(dados).encode()
        return self._abrir(caminho, corpo, headers, "POST")

    def _abrir(self, caminho, corpo=None, headers=None, metodo="GET"):
        request = urllib.request.Request(
            BASE + caminho, data=corpo, headers=headers or {}, method=metodo
        )
        try:
            with self.opener.open(request) as resposta:
                return resposta.status, resposta.geturl(), resposta.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as erro:
            return erro.code, caminho, erro.read().decode("utf-8", "replace")

    def csrf(self) -> str:
        for cookie in self.jar:
            if cookie.name == "csrftoken":
                return cookie.value
        return ""

    def token_formulario(self, caminho: str = "/entrar/") -> str:
        _, _, html = self.get(caminho)
        achado = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
        return achado.group(1) if achado else ""


class SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    print(f"Jornada do médico no primeiro acesso — {BASE}\n")

    print("1) Login com a senha inicial padrão")
    nav = Navegador()
    token = nav.token_formulario()
    status, url, _ = nav.post(
        "/entrar/",
        {
            "csrfmiddlewaretoken": token,
            "cpf": CPF,
            "senha": SENHA_INICIAL,
        },
    )
    verificar(status == 200, "login aceito", f"(HTTP {status})")

    if "/trocar-senha/" in url:
        verificar(
            True,
            "o sistema exige a troca de senha no primeiro acesso",
            f"-> {url.replace(BASE, '')}",
        )
    else:
        print(
            "  (troca de senha desativada neste ambiente — "
            "EXIGIR_TROCA_SENHA=false)"
        )

    print("\n2) A troca de senha")
    if "/trocar-senha/" in url:
        token = nav.token_formulario("/trocar-senha/")
        status, url, _ = nav.post(
            "/trocar-senha/",
            {
                "csrfmiddlewaretoken": token,
                "old_password": SENHA_INICIAL,
                "new_password1": SENHA_NOVA,
                "new_password2": SENHA_NOVA,
            },
        )
        verificar(status == 200, "senha alterada", f"(HTTP {status})")
        verificar(
            "/trocar-senha/" not in url,
            "redirecionado após a troca",
            f"-> {url.replace(BASE, '')}",
        )
    else:
        print("  (dispensada — a senha permanece a inicial)")

    print("\n3) Pagina inicial (apresentacao da ESEM) e area do aluno")
    status, url, html = nav.get("/")
    verificar(
        status == 200 and "Escola Superior de Ética Médica" in html,
        "pagina inicial publica apresenta a ESEM",
        f"(HTTP {status})",
    )
    verificar(
        "17º" not in html and "17&ordm;" not in html,
        "a pagina inicial nao cita curso especifico",
    )
    status, url, html = nav.get("/cursos/")
    verificar(
        status == 200 and "17" in html, "área do aluno lista o curso", f"(HTTP {status})"
    )
    status, url, html = nav.get("/cursos/1/")
    verificar(status == 200, "página do curso abre", f"(HTTP {status})")
    if NOME:
        verificar(NOME in html, "nome do médico exibido")

    print("\n4) Reprodução dos vídeos")
    for video_id, esperado in ((1, True), (2, False)):
        status, _, corpo = nav.get(f"/api/player/state/?enrollment=1&video={video_id}")
        estado = json.loads(corpo)
        verificar(
            estado["unlocked"] == esperado,
            f"vídeo {video_id} {'liberado' if esperado else 'bloqueado (sequencial)'}",
            f"(unlocked={estado['unlocked']})",
        )
        verificar(
            estado["last_position"] == 0 and estado["furthest"] == 0,
            f"vídeo {video_id} começa do zero",
            f"(posição={estado['last_position']} furthest={estado['furthest']})",
        )
        if not esperado:
            continue

        verificar(bool(estado["manifest_url"]), "manifesto do vídeo liberado")
        status, _, mestre = nav.get(estado["manifest_url"])
        variantes = [linha for linha in mestre.splitlines() if "index.m3u8" in linha]
        verificar(len(variantes) > 0, f"master lista {len(variantes)} variante(s)")
        token = variantes[0].split("token=")[1]
        qualidade = urllib.parse.urlparse(variantes[0]).path.split("/")[4]
        status, _, variante = nav.get(
            f"/api/media/{video_id}/{qualidade}/index.m3u8?token={token}"
        )
        segmentos = [linha for linha in variante.splitlines() if linha.startswith("/api/media/")]
        verificar(
            len(segmentos) > 0,
            f"variante assinada com {len(segmentos)} segmentos",
        )

        primeiro = segmentos[0]
        partes = urllib.parse.urlparse(primeiro)
        status, _, _ = nav.get(partes.path + "?" + partes.query)
        verificar(status == 200, "primeiro segmento é entregue (reprodução liberada)")

        ultimo = segmentos[-1]
        partes = urllib.parse.urlparse(ultimo)
        status, _, _ = nav.get(partes.path + "?" + partes.query)
        verificar(status == 403, "segmento no fim do vídeo continua bloqueado")

    print("\n5) Progresso inicial do curso")
    status, _, corpo = nav.get("/api/player/state/?enrollment=1&video=1")
    estado = json.loads(corpo)
    verificar(
        estado["course_percent"] == 0 and estado["required_percent"] == 50,
        "curso começa em 0% (exige 50%)",
        f"(percentual={estado['course_percent']} exigido={estado['required_percent']})",
    )

    print()
    if FALHAS:
        print(f"FALHAS: {len(FALHAS)}")
        for falha in FALHAS:
            print(f"  - {falha}")
        return 1
    print("Jornada validada com sucesso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
