#!/bin/sh
# Extrai quadros-chave dos videos do curso (a partir do HLS) para analise.
set -e
SAIDA=/tmp/quadros
mkdir -p "$SAIDA"

extrair() {
  video="$1"; rotulo="$2"; tempos="$3"
  for t in $tempos; do
    nome="${rotulo}_$(echo "$t" | tr ':' '-').jpg"
    ffmpeg -v error -y -ss "$t" -i "/srv/media/hls/$video/720p/index.m3u8" \
      -frames:v 1 -q:v 3 "$SAIDA/$nome" 2>/dev/null || echo "falhou: $video $t"
    echo "  $nome"
  done
}

echo "--- Encontro 1 ---"
extrair 1 E1 "00:00:20 00:01:00 00:03:00 00:10:00 00:45:00 02:00:00 03:30:00"
echo "--- Encontro 2 ---"
extrair 2 E2 "00:00:20 00:01:00 00:03:00 00:10:00 00:45:00 01:30:00 02:30:00"
ls -la "$SAIDA"
