"""Confere se todas as classes usadas nos templates existem no CSS compilado.

Uso (na raiz do projeto):  python scripts/conferir_css.py
"""

from __future__ import annotations

import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CSS = RAIZ / "static" / "css" / "tailwind.css"

IGNORAR = {
    "hidden", "block", "flex", "grid", "table", "sr-only",
}

PADRAO_CLASSE = re.compile(r'class="([^"]+)"')
DINAMICO = re.compile(r"\{\{|\{%")


def main() -> int:
    css = CSS.read_text(encoding="utf-8")
    # Classes do Tailwind aparecem escapadas no CSS (ex.: .md\:flex)
    disponiveis = set(re.findall(r"\.((?:[a-zA-Z0-9_-]|\\.)+)", css))
    disponiveis = {nome.replace("\\", "") for nome in disponiveis}

    faltando: dict[str, set[str]] = {}
    total = 0

    for template in sorted((RAIZ / "templates").rglob("*.html")):
        texto = template.read_text(encoding="utf-8")
        for bloco in PADRAO_CLASSE.findall(texto):
            if DINAMICO.search(bloco):
                continue
            for classe in bloco.split():
                if not classe or classe in IGNORAR:
                    continue
                total += 1
                if classe not in disponiveis:
                    faltando.setdefault(str(template.relative_to(RAIZ)), set()).add(classe)

    print(f"{total} classes estáticas analisadas em {len(list((RAIZ / 'templates').rglob('*.html')))} templates")
    if not faltando:
        print("OK — todas as classes estáticas foram encontradas no CSS compilado.")
        return 0

    print("\nClasses ausentes no CSS (verifique se são dinâmicas ou erros):")
    for arquivo, classes in faltando.items():
        print(f"  {arquivo}: {', '.join(sorted(classes))}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
