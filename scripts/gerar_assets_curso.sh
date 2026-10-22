#!/bin/sh
# Gera os assets do curso a partir das gravacoes:
#   - emblema da ESEM em branco (silhueta a partir do canal alfa da logo oficial)
#   - imagem de capa do curso (slide de abertura do Encontro 1)
#   - miniaturas (posters) dos videos
#
# Uso (no container app, que tem ffmpeg e os volumes media/ e static/):
#   sh scripts/gerar_assets_curso.sh
set -e

MEDIA=/srv/media
STATIC=/app/static/img
HLS="$MEDIA/hls"

mkdir -p "$STATIC" "$MEDIA/cortes"

echo "1) emblema da ESEM em branco (para o fundo com gradiente)"
ffmpeg -v error -y -i "$STATIC/emblema-esem.png" \
  -vf "format=rgba,geq=r='255':g='255':b='255':a='alpha(X,Y)'" \
  -frames:v 1 "$STATIC/emblema-esem-branca.png"

echo "2) capa do curso = slide de abertura do Encontro 1 (00:09:00)"
ffmpeg -v error -y -ss 00:09:00 -i "$HLS/1/720p/index.m3u8" \
  -frames:v 1 -q:v 3 "$MEDIA/cortes/capa-17-curso.jpg"

echo "3) miniaturas dos videos"
ffmpeg -v error -y -ss 00:09:00 -i "$HLS/1/720p/index.m3u8" \
  -frames:v 1 -q:v 3 "$HLS/1/poster.jpg"
echo "   poster do video 1 (slide de abertura do curso)"
ffmpeg -v error -y -ss 00:00:05 -i "$HLS/2/720p/index.m3u8" \
  -frames:v 1 -q:v 3 "$HLS/2/poster.jpg"
echo "   poster do video 2 (primeira palestra)"

ls -la "$STATIC/emblema-esem-branca.png" "$MEDIA/cortes/capa-17-curso.jpg"
