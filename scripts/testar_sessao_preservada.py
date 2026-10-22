"""Confere que a sessao do navegador sobrevive ao reset de senha (dev)."""

import http.cookiejar
import json
import re
import subprocess
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

    def estado():
        r = op.open(BASE + "/api/player/state/?enrollment=1&video=1")
        return json.loads(r.read().decode())

    antes = estado()
    print(f"1) sessao ativa antes do reset: duracao={antes['duration']}")

    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "app",
            "python",
            "manage.py",
            "cenario_inicial",
            "--cpf",
            CPF,
            "--curso-id",
            "1",
            "--resetar-senha",
            "--zerar-progresso",
        ],
        capture_output=True,
        check=True,
    )

    depois = estado()
    print(f"2) sessao ativa depois do reset: duracao={depois['duration']}")
    ok = antes["duration"] == depois["duration"] and depois["duration"] > 0
    print("OK: a sessao do navegador sobrevive ao reset de senha." if ok else "FALHOU")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
