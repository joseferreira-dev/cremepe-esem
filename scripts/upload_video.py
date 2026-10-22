"""Envia um arquivo de vídeo para um vídeo do portal, usando a API de blocos.

Uso (a partir da máquina do servidor, com o portal no ar):

    python scripts/upload_video.py --admin admin --senha SUA_SENHA \
        --video 1 /caminho/para/aula.mp4

O envio é retomável: se a conexão cair, basta executar o comando novamente —
os blocos já enviados são reaproveitados.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
CHUNK_SIZE = 16 * 1024 * 1024
TIMEOUT = 300  # segundos por requisição


class Cliente:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def csrf_cookie(self) -> str:
        for cookie in self.jar:
            if cookie.name == "csrftoken":
                return cookie.value
        return ""

    def pedir(self, caminho: str, dados=None, metodo="GET", headers_extra=None, form=False):
        url = f"{BASE_URL}{caminho}"
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if headers_extra:
            headers.update(headers_extra)
        if metodo not in ("GET", "HEAD"):
            headers.setdefault("X-CSRFToken", self.csrf_cookie())
        corpo = None
        if dados is not None:
            if isinstance(dados, (bytes, bytearray)):
                headers.setdefault("Content-Type", "application/octet-stream")
                corpo = bytes(dados)
            elif form:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                corpo = urllib.parse.urlencode(dados).encode()
            else:
                headers["Content-Type"] = "application/json"
                corpo = json.dumps(dados).encode()
        request = urllib.request.Request(url, data=corpo, headers=headers, method=metodo)
        try:
            with self.opener.open(request, timeout=TIMEOUT) as resposta:
                return resposta.status, resposta.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as erro:
            return erro.code, erro.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            return 0, f"erro de rede: {erro}"


def token_csrf(cliente: Cliente, caminho: str = "/admin/login/") -> str:
    _, html = cliente.pedir(caminho)
    achado = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    return achado.group(1) if achado else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin", default="admin")
    parser.add_argument("--senha", required=True)
    parser.add_argument("--video", type=int, required=True)
    parser.add_argument("arquivo")
    argumentos = parser.parse_args()

    arquivo = argumentos.arquivo
    if not os.path.isfile(arquivo):
        print(f"Arquivo não encontrado: {arquivo}", file=sys.stderr)
        return 1

    tamanho = os.path.getsize(arquivo)
    cliente = Cliente()

    token = token_csrf(cliente)
    status, corpo = cliente.pedir(
        "/admin/login/",
        {
            "csrfmiddlewaretoken": token,
            "username": argumentos.admin,
            "password": argumentos.senha,
            "next": "/admin/",
        },
        metodo="POST",
        form=True,
    )
    if status != 200:
        print(f"Falha no login do admin (HTTP {status}): {corpo[:200]}", file=sys.stderr)
        return 1

    status, corpo = cliente.pedir(
        "/api/uploads/init/",
        {
            "video_id": argumentos.video,
            "filename": os.path.basename(arquivo),
            "size": tamanho,
        },
        metodo="POST",
    )
    if status != 200:
        print(f"Falha ao iniciar o envio (HTTP {status}): {corpo[:300]}", file=sys.stderr)
        return 1

    sessao = json.loads(corpo)
    upload_id = sessao["upload_id"]
    chunk_size = sessao["chunk_size"]
    total_chunks = sessao["total_chunks"]
    recebidos = set(sessao.get("received") or [])
    print(f"Upload {upload_id}: {total_chunks} blocos de {chunk_size} bytes")
    if sessao.get("retomado"):
        print(f"Retomando envio: {len(recebidos)} bloco(s) já gravado(s).")

    with open(arquivo, "rb") as handle:
        for indice in range(total_chunks):
            if indice in recebidos:
                continue
            handle.seek(indice * chunk_size)
            bloco = handle.read(chunk_size)

            tentativas = 0
            while True:
                tentativas += 1
                status, corpo = cliente.pedir(
                    f"/api/uploads/{upload_id}/chunk/",
                    bloco,
                    metodo="POST",
                    headers_extra={
                        "Content-Type": "application/octet-stream",
                        "X-Chunk-Index": str(indice),
                    },
                )
                if status == 200:
                    break
                if tentativas >= 3:
                    print(
                        f"Falha no bloco {indice} (HTTP {status}): {corpo[:300]}",
                        file=sys.stderr,
                    )
                    return 1
                print(
                    f"  bloco {indice + 1} falhou ({status}); tentando de novo...",
                    file=sys.stderr,
                )
                time.sleep(3)
            print(f"  bloco {indice + 1}/{total_chunks} enviado", end="\r")

    status, corpo = cliente.pedir(
        f"/api/uploads/{upload_id}/complete/", {}, metodo="POST"
    )
    if status != 200:
        print(f"Falha ao concluir (HTTP {status}): {corpo[:300]}", file=sys.stderr)
        return 1

    print("\nArquivo enviado. O vídeo entrou na fila de processamento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
