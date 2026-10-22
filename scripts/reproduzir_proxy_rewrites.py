"""Simula o ambiente que reescreve o cabecalho Cookie das requisicoes fetch.

Cenario: um proxy/extensao reescreve o cabecalho `Cookie` com valores antigos.
A navegacao normal (GET /cursos/1/) chega intacta ao servidor, mas as chamadas
`fetch` do player chegam sem o cookie de sessao valido.

Com o **token de API** (assinado, entregue no HTML da pagina), o player continua
funcionando: o servidor valida a assinatura em vez de depender do cookie.

    python scripts/reproduzir_proxy_rewrites.py
"""

import http.client
import json
import re
import sys
import urllib.parse

from _env import exigir

HOST = "localhost"
PORT = 8080
CPF = exigir("MEDICO_CPF")
SENHA = exigir("MEDICO_SENHA")
NOME_SESSAO = "cremepe_sid"
NOME_CSRF = "cremepe_csrftoken"

# Cookies "antigos" que o proxy reescreve no lugar dos atuais.
COOKIE_REESCRITO = (
    "__stripe_mid=a44465f9-dda0-4231-bff8-0b82e77d504fa09e70; "
    "csrftoken=WyCrkN1nq4BxzkoKc83mn9V4g6RTnvdT; "
    "sessionid=a1vdmxrddkpx8v9sq8t4mg4lor205ief"
)

FALHAS: list[str] = []


def verificar(condicao, descricao, detalhe=""):
    print(f"  [{'ok' if condicao else 'FALHA'}] {descricao} {detalhe}")
    if not condicao:
        FALHAS.append(descricao)


def requisitar(caminho, cookie=None, metodo="GET", corpo=None, headers=None):
    conexao = http.client.HTTPConnection(HOST, PORT, timeout=15)
    cab = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"http://{HOST}:{PORT}/cursos/1/",
        "User-Agent": "Mozilla/5.0 Chrome/156.0.0.0",
    }
    if cookie:
        cab["Cookie"] = cookie
    cab.update(headers or {})
    conexao.request(metodo, caminho, body=corpo, headers=cab)
    resposta = conexao.getresponse()
    return (
        resposta.status,
        resposta.read().decode("utf-8", "replace"),
        resposta.getheaders(),
    )


def main():
    print("1) Login e leitura do token de API da pagina do curso")
    status, html, cabecalhos_get = requisitar("/entrar/")
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
    cookies_get = [v for k, v in cabecalhos_get if k.lower() == "set-cookie"]
    csrftoken = re.search(rf"{NOME_CSRF}=([^;]+)", " ".join(cookies_get)).group(1)

    status, _, cabecalhos = requisitar(
        "/entrar/",
        cookie=f"{NOME_CSRF}={csrftoken}",
        metodo="POST",
        corpo=urllib.parse.urlencode(
            {"csrfmiddlewaretoken": token, "cpf": CPF, "senha": SENHA}
        ).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    cookies_login = [v for k, v in cabecalhos if k.lower() == "set-cookie"]
    sessao = re.search(rf"{NOME_SESSAO}=([^;]+)", " ".join(cookies_login)).group(1)
    print(f"   sessao criada: {sessao[:12]}...")

    cookie_navegador = f"{NOME_SESSAO}={sessao}; {NOME_CSRF}={csrftoken}"
    status, html, _ = requisitar("/cursos/1/", cookie=cookie_navegador)
    api_token = re.search(r'name="api-token" content="([^"]+)"', html)
    verificar(
        api_token is not None,
        "a pagina entrega o token de API",
        "" if api_token else "(meta api-token ausente)",
    )
    if not api_token:
        return 1
    token_api = api_token.group(1)

    print("\n2) Chamadas do player com o Cookie REESCRITO (sem a sessao valida)")
    cabecalhos_api = {"Authorization": f"Bearer {token_api}"}

    status, corpo, _ = requisitar(
        "/api/player/state/?enrollment=1&video=1",
        cookie=COOKIE_REESCRITO,
        headers=cabecalhos_api,
    )
    verificar(
        status == 200,
        "a API responde 200 usando apenas o token",
        f"(HTTP {status})",
    )
    if status == 200:
        dados = json.loads(corpo)
        verificar(
            dados.get("duration", 0) > 0,
            "o estado do video foi devolvido",
            f"(duracao={dados.get('duration')})",
        )

    print("\n3) Heartbeat (POST) com o Cookie reescrito")
    status, corpo, _ = requisitar(
        "/api/player/heartbeat/",
        cookie=COOKIE_REESCRITO,
        metodo="POST",
        corpo=json.dumps(
            {"enrollment": 1, "video": 1, "position": 0, "playing": False}
        ).encode(),
        headers={**cabecalhos_api, "Content-Type": "application/json"},
    )
    verificar(status == 200, "o heartbeat e aceito", f"(HTTP {status})")

    print("\n4) Manifesto e segmentos (hls.js) com o token")
    status, corpo, _ = requisitar(
        "/api/player/state/?enrollment=1&video=1",
        cookie=COOKIE_REESCRITO,
        headers=cabecalhos_api,
    )
    estado = json.loads(corpo)
    status, mestre, _ = requisitar(
        estado["manifest_url"],
        cookie=COOKIE_REESCRITO,
        headers=cabecalhos_api,
    )
    verificar(status == 200, "o manifesto e entregue", f"(HTTP {status})")

    variantes = [l for l in mestre.splitlines() if "index.m3u8" in l]
    verificar(len(variantes) > 0, f"o manifesto lista {len(variantes)} variante(s)")

    token_variante = variantes[0].split("token=")[1]
    qualidade = urllib.parse.urlparse(variantes[0]).path.split("/")[4]
    caminho_variante = urllib.parse.urlparse(variantes[0]).path
    status, variante, _ = requisitar(
        f"{caminho_variante}?token={token_variante}",
        cookie=COOKIE_REESCRITO,
        headers=cabecalhos_api,
    )
    segmentos = [l for l in variante.splitlines() if l.startswith("/api/media/")]
    verificar(
        status == 200 and len(segmentos) > 0,
        f"a variante traz {len(segmentos)} segmentos",
        f"(HTTP {status})",
    )

    if segmentos:
        print("\n5) Baixa de um segmento (o video propriamente dito)")
        partes = urllib.parse.urlparse(segmentos[0])
        status, dados, _ = requisitar(
            partes.path + "?" + partes.query,
            cookie=COOKIE_REESCRITO,
            headers=cabecalhos_api,
        )
        magic = ""
        if isinstance(dados, str):
            magic = dados[:1].encode("latin-1", "ignore").hex()
        verificar(
            status == 200 and len(dados) > 1000,
            "o segmento de video e entregue",
            f"(HTTP {status}, {len(dados)} bytes, magic={magic})",
        )

    print()
    if FALHAS:
        print(f"FALHAS: {len(FALHAS)}")
        for f in FALHAS:
            print("  -", f)
        return 1
    print("O player funciona mesmo com o cabecalho Cookie reescrito.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
