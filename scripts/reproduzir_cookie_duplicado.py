"""Reproduz o cenario real: o navegador envia cookies NOVOS e ANTIGOS no mesmo header.

O Chrome envia todos os cookies cujo path casa com a URL. Se um cookie antigo
(duplicado) permanece no navegador, o Django le o primeiro do cabecalho e pode
cair numa sessao que nao existe mais — o medico fica "deslogado" mesmo apos um
login bem-sucedido.

Este script monta o cabecalho na mao (como o navegador) e confere que o
servidor escolhe a sessao valida.

    python scripts/reproduzir_cookie_duplicado.py
"""

import http.client
import json
import re
import sys
import urllib.parse

from _env import exigir

BASE_HOST = "localhost"
BASE_PORT = 8080
CPF = exigir("MEDICO_CPF")
SENHA = exigir("MEDICO_SENHA")
NOME_SESSAO = "cremepe_sid"
NOME_CSRF = "cremepe_csrftoken"

FALHAS: list[str] = []


def verificar(condicao, descricao, detalhe=""):
    print(f"  [{'ok' if condicao else 'FALHA'}] {descricao} {detalhe}")
    if not condicao:
        FALHAS.append(descricao)


def requisitar(caminho, cookie=None, metodo="GET", corpo=None, headers=None):
    conexao = http.client.HTTPConnection(BASE_HOST, BASE_PORT, timeout=15)
    cab = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"http://{BASE_HOST}:{BASE_PORT}/cursos/1/",
        "User-Agent": "Mozilla/5.0 Chrome/156.0.0.0",
    }
    if cookie:
        cab["Cookie"] = cookie
    cab.update(headers or {})
    conexao.request(metodo, caminho, body=corpo, headers=cab)
    resposta = conexao.getresponse()
    dados = resposta.read().decode("utf-8", "replace")
    return resposta.status, dados, resposta.getheaders()


def main():
    print("1) Login (obtem a sessao valida e o csrftoken novo)")
    status, html, cabecalhos_get = requisitar("/entrar/")
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
    cookies_get = [v for k, v in cabecalhos_get if k.lower() == "set-cookie"]
    csrftoken_get = re.search(rf"{NOME_CSRF}=([^;]+)", " ".join(cookies_get)).group(1)

    status, _, cabecalhos = requisitar(
        "/entrar/",
        cookie=f"{NOME_CSRF}={csrftoken_get}",
        metodo="POST",
        corpo=urllib.parse.urlencode(
            {"csrfmiddlewaretoken": token, "cpf": CPF, "senha": SENHA}
        ).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    cookies_login = [v for k, v in cabecalhos if k.lower() == "set-cookie"]
    sessionid_novo = re.search(rf"{NOME_SESSAO}=([^;]+)", " ".join(cookies_login)).group(1)
    csrftoken_novo = re.search(rf"{NOME_CSRF}=([^;]+)", " ".join(cookies_login)).group(1)
    print(f"   cookie de sessao novo: {sessionid_novo[:12]}...")

    print("\n2) Navegador com cookie ANTIGO (orfao) + cookie NOVO no mesmo header")
    orfao = "a1vdmxrddkpx8v9sq8t4mg4lor205ief"
    cookie = (
        f"__stripe_mid=a44465f9-dda0-4231-bff8-0b82e77d504fa09e70; "
        f"{NOME_CSRF}=WyCrkN1nq4BxzkoKc83mn9V4g6RTnvdT; {NOME_SESSAO}={orfao}; "
        f"{NOME_CSRF}={csrftoken_novo}; {NOME_SESSAO}={sessionid_novo}"
    )

    status, corpo, _ = requisitar(
        "/api/player/state/?enrollment=1&video=1", cookie=cookie
    )
    verificar(
        status == 200,
        "a API usa a sessao VALIDA mesmo com o cookie orfao no cabecalho",
        f"(HTTP {status})",
    )
    if status == 200:
        dados = json.loads(corpo)
        verificar(
            dados.get("duration", 0) > 0,
            "o estado do video foi devolvido",
            f"(duracao={dados.get('duration')})",
        )

    print("\n3) A mesma coisa na pagina do curso")
    status, corpo, _ = requisitar("/cursos/1/", cookie=cookie)
    verificar(status == 200, "a pagina do curso abre normalmente", f"(HTTP {status})")

    print("\n4) Heartbeat (POST) tambem deve passar")
    status, corpo, _ = requisitar(
        "/api/player/heartbeat/",
        cookie=cookie,
        metodo="POST",
        corpo=json.dumps(
            {"enrollment": 1, "video": 1, "position": 0, "playing": False}
        ).encode(),
        headers={"Content-Type": "application/json", "X-CSRFToken": csrftoken_novo},
    )
    verificar(status == 200, "o heartbeat e aceito", f"(HTTP {status})")

    print("\n5) Cookies padrao do Django sao ignorados (o proxy os reescreve)")
    status, corpo, _ = requisitar(
        "/api/player/state/?enrollment=1&video=1",
        cookie=f"sessionid={orfao}; csrftoken=WyCrkN1nq4BxzkoKc83mn9V4g6RTnvdT",
    )
    verificar(
        status == 401,
        "cookies 'sessionid'/'csrftoken' padrao nao autenticam",
        f"(HTTP {status})",
    )

    print()
    if FALHAS:
        print(f"FALHAS: {len(FALHAS)}")
        for f in FALHAS:
            print("  -", f)
        return 1
    print("Cenario do cookie duplicado resolvido pelo servidor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
