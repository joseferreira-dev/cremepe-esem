"""QR Code em data URI — compartilhado pela página do curso e pelo certificado."""

from __future__ import annotations

import base64
import functools
import io


@functools.lru_cache(maxsize=32)
def qr_data_uri(url: str) -> str:
    """PNG do QR Code apontando para ``url``, como ``data:image/png;base64,...``.

    Memoizado: a URL da pesquisa é fixa e a página do curso gera o mesmo QR
    a cada requisição.
    """
    import segno

    buffer = io.BytesIO()
    segno.make(url).save(buffer, kind="png", scale=8, border=4)
    return "data:image/png;base64,%s" % base64.b64encode(buffer.getvalue()).decode(
        "ascii"
    )
