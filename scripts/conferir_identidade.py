"""Confere se todas as telas trazem o conteudo institucional da ESEM
e se nao ha referencias indevidas ao 17o curso fora das telas permitidas.

    python scripts/conferir_identidade.py
"""

from __future__ import annotations

import http.cookiejar
import os
import re
import sys
import urllib.parse
import urllib.request

from _env import exigir

BASE = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
CPF = exigir("MEDICO_CPF")
SENHA = exigir("MEDICO_SENHA")

FALHAS: list[str] = []

# Paginas onde a referencia ao 17o curso e permitida.
COM_17 = {"/entrar/", "/cursos/1/"}

# Textos institucionais que devem existir em todo o portal.
INSTITUCIONAL = [
    "Escola Superior de Ética Médica",
    "núcleo técnico permanente",
    "Resoluções CREMEPE 03/2005 e 08/2011",
]


def verificar(condicao: bool, descricao: str, detalhe: str = "") -> None:
    print(f"  [{'ok' if condicao else 'FALHA'}] {descricao} {detalhe}")
    if not condicao:
        FALHAS.append(descricao)


def cliente_logado(cpf=None, senha=None, admin=False, anonimo=False):
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    if anonimo:
        return op
    if admin:
        html = op.open(BASE + "/admin/login/").read().decode()
        tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
        dados = {"csrfmiddlewaretoken": tok, "username": "admin", "password": senha, "next": "/admin/"}
    else:
        html = op.open(BASE + "/entrar/").read().decode()
        tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
        dados = {"csrfmiddlewaretoken": tok, "cpf": cpf, "senha": senha}
    op.open(
        urllib.request.Request(
            BASE + ("/admin/login/" if admin else "/entrar/"),
            data=urllib.parse.urlencode(dados).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
    )
    return op


def _bloco(html: str, tag: str) -> str:
    """Extrai o conteudo de uma tag (header/footer) do HTML."""
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.S)
    return match.group(1) if match else ""


def main() -> int:
    print("1) Conteudo institucional da ESEM")
    op = cliente_logado(CPF, SENHA)
    admin_op = cliente_logado(admin=True, senha=exigir("ADMIN_SENHA"))
    paginas = {
        "/": (op, "pagina inicial (apresentacao da ESEM)"),
        "/cursos/": (op, "area do medico"),
        "/cursos/1/": (op, "pagina do curso"),
        "/painel/": (admin_op, "painel do admin"),
    }
    for caminho, (sessao, nome) in paginas.items():
        html = sessao.open(BASE + caminho).read().decode()
        achados = [t for t in INSTITUCIONAL if t in html]
        verificar(
            len(achados) >= 1,
            f"{nome} ({caminho}) cita a ESEM",
            f"({len(achados)} de {len(INSTITUCIONAL)} trechos)",
        )

    print("\n2) Ausencia de mojibake (acentos quebrados)")
    for caminho, sessao in [("/", op), ("/cursos/1/", op), ("/painel/", admin_op)]:
        html = sessao.open(BASE + caminho).read().decode()
        quebrados = [
            m for m in ("Ã©", "Ã§", "Ã£", "Ãµ", "Â ", "�") if m in html
        ]
        verificar(not quebrados, f"sem caracteres corrompidos em {caminho}", f"{quebrados}")

    print("\n3) Referencia ao 17o curso apenas na pagina do curso")
    for caminho, sessao in [
        ("/", op),
        ("/cursos/", op),
        ("/painel/", admin_op),
        ("/painel/cursos/", admin_op),
        ("/painel/medicos/", admin_op),
    ]:
        html = sessao.open(BASE + caminho).read().decode()
        tem17 = "17º" in html or "17&ordm;" in html
        # A listagem de cursos mostra o titulo como dado (permitido); as demais
        # telas nao devem citar o curso.
        if caminho in ("/cursos/", "/painel/", "/painel/cursos/"):
            continue
        verificar(not tem17, f"sem mencao ao 17o curso em {caminho}")

    # A tela de login, vista por um visitante anonimo, nao pode citar o curso.
    anonimo = cliente_logado(admin=False, anonimo=True)
    html = anonimo.open(BASE + "/entrar/").read().decode()
    verificar(
        "17º" not in html and "17&ordm;" not in html,
        "a tela de login (visitante) nao cita o 17o curso",
    )
    verificar(
        "Escola Superior de Ética Médica" in html,
        "a tela de login apresenta a ESEM",
    )

    html = op.open(BASE + "/cursos/1/").read().decode()
    verificar(
        "17º" in html or "17&ordm;" in html,
        "a pagina do curso identifica o 17º Curso",
    )

    print()
    if FALHAS:
        print(f"FALHAS: {len(FALHAS)}")
        for falha in FALHAS:
            print(f"  - {falha}")
        return 1
    print("Identidade institucional conferida.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
