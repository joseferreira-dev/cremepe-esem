"""Testes do helper de QR Code compartilhado pela web e pelos certificados."""

import base64

from django.test import SimpleTestCase

from apps.web.qrcode import qr_data_uri


class TestQrDataUri(SimpleTestCase):
    def test_gera_png_em_data_uri(self):
        uri = qr_data_uri("https://exemplo.org/validar")
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        png = base64.b64decode(uri.split(",", 1)[1])
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

    def test_mesma_url_gera_o_mesmo_qr(self):
        """Memoização: a página do curso não regenera o QR a cada requisição."""
        self.assertEqual(
            qr_data_uri("https://exemplo.org/pesquisa"),
            qr_data_uri("https://exemplo.org/pesquisa"),
        )
