"""Corrige a codificação (mojibake) de arquivos que passaram pelo PowerShell.

O PowerShell 5.1 lê arquivos UTF-8 como ANSI e grava de volta em UTF-8,
duplicando a codificação de parte dos acentos. Aqui cada trecho afetado
(é, ç, ­...) é decodificado uma vez, voltando ao texto correto.
"""

import pathlib
import re

PADRAO = re.compile(r"(?:Ã[\x80-\xbf]|Â[\x80-\xbf])+")


def corrigir_texto(texto: str) -> str:
    def substituir(encontrado):
        trecho = encontrado.group(0)
        try:
            return trecho.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return trecho

    return PADRAO.sub(substituir, texto)


def main() -> int:
    raiz = pathlib.Path(__file__).resolve().parent.parent
    extensoes = {".html", ".py", ".js", ".css", ".md", ".sh", ".ps1", ".txt"}
    alterados = 0

    for caminho in sorted(raiz.rglob("*")):
        if not caminho.is_file() or caminho.suffix not in extensoes:
            continue
        if any(parte in caminho.parts for parte in {".git", "media", "dbdata", "staticfiles", "tools", "arquivos"}):
            continue

        bruto = caminho.read_bytes()
        try:
            texto = bruto.decode("utf-8")
        except UnicodeDecodeError:
            # Arquivo gravado em ANSI: converte para UTF-8 corrigindo o mojibake.
            texto = bruto.decode("cp1252", errors="replace")
            novo = corrigir_texto(texto)
            caminho.write_text(novo, encoding="utf-8")
            print(f"[reparado] {caminho.relative_to(raiz)} (ansi -> utf-8)")
            alterados += 1
            continue

        novo = corrigir_texto(texto)
        if novo != texto:
            caminho.write_text(novo, encoding="utf-8")
            print(f"[reparado] {caminho.relative_to(raiz)}")
            alterados += 1

    print(f"\n{alterados} arquivo(s) reparado(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
