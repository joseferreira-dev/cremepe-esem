"""Extração do modelo de certificado enviado em DOCX.

O painel permite que cada curso envie o próprio modelo (.docx). O modelo
tem exatamente um campo dinâmico — ``NOME COMPLETO`` — e uma arte de fundo
com as assinaturas. Aqui extraímos os dois para renderizar o PDF via
WeasyPrint (mesma pipeline de renderização), substituindo apenas
o nome do aluno.

Usa só a biblioteca padrão (zipfile + ElementTree): o .docx é um ZIP com
``word/document.xml`` e as imagens em ``word/media/``.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

# Limite do arquivo enviado (o modelo oficial tem ~1 MB).
TAMANHO_MAXIMO = 10 * 1024 * 1024

_PLACEHOLDER = re.compile(r"NOME\s+COMPLETO", re.IGNORECASE)
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# Namespace do mc:AlternateContent (caixas de texto com Choice + Fallback).
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


class ModeloInvalido(Exception):
    """O DOCX enviado não serve como modelo de certificado."""


def _extrair_paragrafos(document_xml: bytes) -> list[str]:
    """Parágrafos visíveis do documento, na ordem, sem duplicatas.

    Dois cuidados para modelos com caixas de texto (o caso real da ESEM):

    * só parágrafos **internos** — o parágrafo externo que hospeda a caixa
      concatenaria todos os textos em uma linha só (run-on);
    * ignora o ``mc:Fallback`` do ``mc:AlternateContent`` — é a cópia de
      compatibilidade que o Word não exibe quando suporta a versão Choice.
    """
    raiz = ET.fromstring(document_xml)
    pais = {filho: pai for pai in raiz.iter() for filho in pai}
    paragrafos: list[str] = []

    for paragrafo in raiz.iter(f"{_W}p"):
        if any(interno is not paragrafo for interno in paragrafo.iter(f"{_W}p")):
            continue  # contém caixa de texto aninhada: não é parágrafo visível
        atual = pais.get(paragrafo)
        em_fallback = False
        while atual is not None:
            if atual.tag == f"{_MC}Fallback":
                em_fallback = True
                break
            atual = pais.get(atual)
        if em_fallback:
            continue
        texto = "".join(node.text or "" for node in paragrafo.iter(f"{_W}t"))
        texto = texto.strip()
        if texto and (not paragrafos or paragrafos[-1] != texto):
            paragrafos.append(texto)
    return paragrafos


def _assinatura_imagem(conteudo: bytes) -> str | None:
    """Extensão da imagem pelo magic number (png ou jpg)."""
    if conteudo.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if conteudo.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return None


def extrair_modelo(conteudo: bytes) -> dict:
    """Devolve ``{"imagem": bytes, "extensao": str, "texto": str}``.

    ``texto`` são os parágravos não vazios do documento, separados por
    linha em branco, mantendo o campo ``NOME COMPLETO`` intacto (a
    substituição acontece na emissão, com o nome do aluno).

    Levanta :class:`ModeloInvalido` quando o arquivo não é um DOCX
    utilizável (zip inválido, sem imagem, sem o campo de nome ou com
    imagem em formato não suportado).
    """
    if not conteudo:
        raise ModeloInvalido("O arquivo enviado está vazio.")
    if len(conteudo) > TAMANHO_MAXIMO:
        raise ModeloInvalido(
            f"O modelo excede {TAMANHO_MAXIMO // (1024 * 1024)} MB."
        )

    try:
        arquivo = zipfile.ZipFile(io.BytesIO(conteudo))
    except zipfile.BadZipFile as erro:
        raise ModeloInvalido(
            "O arquivo não é um DOCX válido. Envie o modelo salvo em "
            "Word (.docx)."
        ) from erro

    with arquivo:
        try:
            document_xml = arquivo.read("word/document.xml")
        except KeyError as erro:
            raise ModeloInvalido(
                "O arquivo não parece um documento do Word (.docx)."
            ) from erro

        midias = {
            nome: arquivo.read(nome)
            for nome in arquivo.namelist()
            if nome.startswith("word/media/") and not nome.endswith("/")
        }

    texto_documento = "\n\n".join(_extrair_paragrafos(document_xml))

    if not _PLACEHOLDER.search(texto_documento):
        raise ModeloInvalido(
            "O modelo precisa conter o campo NOME COMPLETO (é onde o nome "
            "do médico será impresso)."
        )
    if not midias:
        raise ModeloInvalido(
            "O modelo não contém imagem de fundo (arte do certificado)."
        )

    # A arte do certificado é a maior imagem do pacote (as logos são
    # pequenas). A validação de formato evita EMF/SVG que o WeasyPrint
    # não renderiza.
    nome_imagem = max(midias, key=lambda n: len(midias[n]))
    extensao = _assinatura_imagem(midias[nome_imagem])
    if extensao is None:
        suportadas = [
            n for n in midias if _assinatura_imagem(midias[n]) is not None
        ]
        if not suportadas:
            raise ModeloInvalido(
                "A imagem do modelo precisa estar em PNG ou JPEG."
            )
        nome_imagem = max(suportadas, key=lambda n: len(midias[n]))
        extensao = _assinatura_imagem(midias[nome_imagem])

    return {
        "imagem": midias[nome_imagem],
        "extensao": extensao,
        "texto": texto_documento,
    }


def paragrafos_do_modelo(texto: str, aluno: str) -> list[dict]:
    """Divide o texto extraído em parágrafos prontos para o template.

    Cada parágrafo vira ``{"texto": ..., "nome": bool}``; ocorrências de
    ``NOME COMPLETO`` são substituídas pelo nome do aluno.
    """
    resultado = []
    for bloco in (texto or "").split("\n\n"):
        bloco = bloco.strip()
        if not bloco:
            continue
        eh_nome = bool(_PLACEHOLDER.search(bloco))
        substituido = _PLACEHOLDER.sub(aluno, bloco)
        resultado.append({"texto": substituido, "nome": eh_nome})
    return resultado
