"""Reproduz a sequencia do navegador: login -> pagina do curso -> API do player.

    python scripts/reproduzir_sessao.py
"""

import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request

from _env import exigir

BASE = "http://localhost:8080"
CPF = exigir("MEDICO_CPF")
SENHA = exigir("MEDICO_SENHA")


def main():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def get(caminho, headers=None):
        req = urllib.request.Request(BASE + caminho, headers=headers or {})
        try:
            r = op.open(req)
            return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def post(caminho, dados, headers=None):
        req = urllib.request.Request(
            BASE + caminho,
            data=urllib.parse.urlencode(dados).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
            method="POST",
        )
        try:
            r = op.open(req)
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, caminho, e.read().decode("utf-8", "replace")

    def cookies():
        return {c.name: c.value[:10] for c in jar}

    # 1) login (como o navegador: com next)
    html = get("/entrar/?next=/cursos/1/")[1]
    tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
    status, url, _ = post(
        "/entrar/?next=/cursos/1/",
        {"csrfmiddlewaretoken": tok, "cpf": CPF, "senha": SENHA},
    )
    print(f"1) POST /entrar/ -> {status} | destino: {url.replace(BASE, '')}")
    print(f"   cookies: {cookies()}")

    # 2) pagina do curso
    status, html = get("/cursos/1/")
    print(f"2) GET /cursos/1/ -> {status}")
    meta = re.search(r'name="csrf-token" content="([^"]+)"', html)
    print(f"   csrf-token da pagina: {(meta.group(1)[:12] + '...') if meta else 'AUSENTE'}")
    print(f"   cookies: {cookies()}")

    # 3) API do player, como o player.js faz
    status, corpo = get(
        "/api/player/state/?enrollment=1&video=1",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    print(f"3) GET /api/player/state/ -> {status}")
    print(f"   corpo: {corpo[:90]}")
    print(f"   cookies: {cookies()}")

    ok = status == 200
    print("\nOK: a API reconheceu a sessao do navegador." if ok else "\nFALHOU: a API nao reconheceu a sessao.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
