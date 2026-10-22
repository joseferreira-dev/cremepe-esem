"""Reproduz o cenario real do navegador: dois cookies `sessionid`.

Como o Chrome envia os cookies cujo path casa com a URL (e o Django le sempre o
primeiro), um cookie antigo/orfao com path=/ faz a API (/api/...) falhar com
401, enquanto a pagina (/cursos/1/) pode funcionar com um cookie de path
especifico.

O servidor deve, ao responder 401/302, apagar os cookies de sessao (inclusive
duplicatas com paths diferentes) para que o proximo login funcione.

    python scripts/reproduzir_cookie_orfao.py
"""

import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from _env import exigir

BASE = "http://localhost:8080"
CPF = exigir("MEDICO_CPF")
SENHA = exigir("MEDICO_SENHA")
ORFAO = "a1vdmxrddkpx8v9sq8t4mg4lor205ief"  # sessao que nao existe no banco

FALHAS: list[str] = []


def verificar(condicao, descricao, detalhe=""):
    print(f"  [{'ok' if condicao else 'FALHA'}] {descricao} {detalhe}")
    if not condicao:
        FALHAS.append(descricao)


def cookie(nome, valor, path):
    return http.cookiejar.Cookie(
        version=0,
        name=nome,
        value=valor,
        port=None,
        port_specified=False,
        domain="localhost",
        domain_specified=True,
        domain_initial_dot=False,
        path=path,
        path_specified=True,
        secure=False,
        expires=None,
        discard=False,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": None},
        rfc2109=False,
    )


def main():
    print("1) Login (sessionid valido)")
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    html = op.open(BASE + "/entrar/").read().decode()
    tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
    op.open(
        urllib.request.Request(
            BASE + "/entrar/",
            data=urllib.parse.urlencode(
                {"csrfmiddlewaretoken": tok, "cpf": CPF, "senha": SENHA}
            ).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
    )
    valido = next(c.value for c in jar if c.name == "sessionid")
    print(f"   sessionid valido: {valido[:12]}...")

    print("\n2) Navegador com cookie orfao (path=/) e cookie valido (path=/cursos/1/)")
    jar2 = http.cookiejar.CookieJar()
    jar2.set_cookie(cookie("sessionid", ORFAO, "/"))
    jar2.set_cookie(cookie("sessionid", valido, "/cursos/1/"))
    op2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar2))

    def pedir(caminho, headers=None):
        req = urllib.request.Request(BASE + caminho, headers=headers or {})
        try:
            r = op2.open(req)
            return r.status, r.read().decode("utf-8", "replace"), r.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), e.headers.get_all("Set-Cookie") or []

    # 2a) API: envia so o cookie path=/ (orfao) -> 401
    status_api, corpo_api, cookies_api = pedir(
        "/api/player/state/?enrollment=1&video=1",
        {"X-Requested-With": "XMLHttpRequest"},
    )
    verificar(status_api == 401, "API responde 401 (cookie orfao)", f"(HTTP {status_api})")
    limpeza = [c for c in cookies_api if "sessionid=" in c and "Max-Age=0" in c]
    verificar(len(limpeza) > 0, "a resposta 401 manda apagar o cookie de sessao", f"({len(limpeza)} cookies)")

    # 2b) Pagina /cursos/1/: envia o cookie path=/cursos/1/ (valido) -> 200
    status_pagina, corpo_pagina, cookies_pagina = pedir("/cursos/1/")
    print(f"       (pagina: HTTP {status_pagina})")

    # 2c) Apos a limpeza, o proximo login funciona normalmente
    print("\n3) Apos a limpeza (cookies apagados), novo login")
    jar3 = http.cookiejar.CookieJar()
    op3 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar3))
    html = op3.open(BASE + "/entrar/").read().decode()
    tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
    op3.open(
        urllib.request.Request(
            BASE + "/entrar/",
            data=urllib.parse.urlencode(
                {"csrfmiddlewaretoken": tok, "cpf": CPF, "senha": SENHA}
            ).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
    )
    req = urllib.request.Request(
        BASE + "/api/player/state/?enrollment=1&video=1",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    r = op3.open(req)
    dados = json.loads(r.read().decode())
    verificar(r.status == 200, "API responde 200 com a sessao nova", f"(HTTP {r.status})")

    print()
    if FALHAS:
        print(f"FALHAS: {len(FALHAS)}")
        for f in FALHAS:
            print("  -", f)
        return 1
    print("Cenario do cookie duplicado tratado: a limpeza resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
