"""Verifica a entrega real do stream: bytes do segmento, cabeçalhos e playlist.

Simula exatamente o que o hls.js faz no navegador:

    1. player_state  ->  manifest_url
    2. master.m3u8   ->  URL da variante
    3. index.m3u8    ->  URLs dos segmentos
    4. segmento      ->  bytes de vídeo (magic number do MPEG-TS)

    python scripts/verificar_stream.py
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
SENHA = exigir("MEDICO_SENHA")
SENHA_NOVA = exigir("MEDICO_SENHA_NOVA")
MATRICULA = os.environ.get("MATRICULA_ID", "1")
VIDEO = os.environ.get("VIDEO_ID", "1")

FALHAS: list[str] = []


def verificar(condicao: bool, descricao: str, detalhe: str = "") -> None:
    print(f"  [{'ok' if condicao else 'FALHA'}] {descricao} {detalhe}")
    if not condicao:
        FALHAS.append(descricao)


class Cliente:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def pedir(self, caminho: str, metodo: str = "GET"):
        request = urllib.request.Request(
            BASE + caminho, headers={"X-Requested-With": "XMLHttpRequest"}
        )
        try:
            with self.opener.open(request) as resposta:
                return resposta.status, dict(resposta.headers), resposta.read()
        except urllib.error.HTTPError as erro:
            return erro.code, dict(erro.headers), erro.read()

    def csrf(self) -> str:
        for cookie in self.jar:
            if cookie.name == "csrftoken":
                return cookie.value
        return ""

    def login(self):
        _, _, html = self.pedir("/entrar/")
        token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"', html.decode()
        ).group(1)
        dados = urllib.parse.urlencode(
            {"csrfmiddlewaretoken": token, "cpf": CPF, "senha": SENHA}
        ).encode()
        request = urllib.request.Request(
            BASE + "/entrar/",
            data=dados,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with self.opener.open(request) as resposta:
            destino = resposta.geturl()

        if "/trocar-senha/" in destino:
            # Primeira entrada: troca obrigatória. Realiza a troca para seguir.
            _, _, html = self.pedir("/trocar-senha/")
            token = re.search(
                r'name="csrfmiddlewaretoken" value="([^"]+)"', html.decode()
            ).group(1)
            dados = urllib.parse.urlencode(
                {
                    "csrfmiddlewaretoken": token,
                    "old_password": SENHA,
                    "new_password1": SENHA_NOVA,
                    "new_password2": SENHA_NOVA,
                }
            ).encode()
            request = urllib.request.Request(
                BASE + "/trocar-senha/",
                data=dados,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with self.opener.open(request) as resposta:
                destino = resposta.geturl()
            print(f"  (senha inicial trocada por {SENHA_NOVA} para este teste)")
        return destino


def main() -> int:
    print(f"Verificação do stream — {BASE}\n")
    cliente = Cliente()
    destino = cliente.login()
    if "/entrar/" in destino:
        print("  FALHA no login: CPF ou senha inválidos.")
        return 1

    print("1) player_state")
    status, _, corpo = cliente.pedir(
        f"/api/player/state/?enrollment={MATRICULA}&video={VIDEO}"
    )
    estado = json.loads(corpo)
    manifest = estado["manifest_url"]
    verificar(status == 200 and bool(manifest), "estado do player", f"(HTTP {status})")

    print("\n2) master.m3u8")
    status, cabecalhos, corpo = cliente.pedir(manifest)
    mestre = corpo.decode()
    variantes = [l for l in mestre.splitlines() if "index.m3u8" in l]
    verificar(status == 200 and len(variantes) > 0, "manifesto mestre", f"(HTTP {status})")
    verificar(
        "mpegurl" in cabecalhos.get("Content-Type", ""),
        "content-type do manifesto",
        f"({cabecalhos.get('Content-Type')})",
    )

    print("\n3) index.m3u8 (variante)")
    token = variantes[0].split("token=")[1]
    qualidade = urllib.parse.urlparse(variantes[0]).path.split("/")[4]
    status, cabecalhos, corpo = cliente.pedir(
        f"/api/media/{VIDEO}/{qualidade}/index.m3u8?token={token}"
    )
    variante = corpo.decode()
    segmentos = [l for l in variante.splitlines() if l.startswith("/api/media/")]
    verificar(status == 200 and len(segmentos) > 0, f"variante ({len(segmentos)} segmentos)")
    verificar(
        "#EXTINF" in variante and "#EXT-X-TARGETDURATION" in variante,
        "playlist no formato HLS esperado pelo hls.js",
    )

    print("\n4) segmento (bytes de vídeo)")
    partes = urllib.parse.urlparse(segmentos[0])
    status, cabecalhos, corpo = cliente.pedir(partes.path + "?" + partes.query)
    verificar(status == 200, f"status do segmento (HTTP {status})")
    verificar(len(corpo) > 1000, "segmento com conteúdo", f"({len(corpo)} bytes)")
    verificar(corpo[:1] == b"\x47", "magic number MPEG-TS (0x47)", f"({corpo[:1].hex()})")
    verificar(
        cabecalhos.get("Content-Type", "").startswith("video/mp2t"),
        "content-type do segmento",
        f"({cabecalhos.get('Content-Type')})",
    )

    print("\n5) posters")
    status, cabecalhos, corpo = cliente.pedir(f"/api/media/{VIDEO}/poster.jpg")
    verificar(status == 200 and len(corpo) > 1000, "poster do vídeo", f"({len(corpo)} bytes)")

    print()
    if FALHAS:
        print(f"FALHAS: {len(FALHAS)}")
        for falha in FALHAS:
            print(f"  - {falha}")
        return 1
    print("Stream entregue corretamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
