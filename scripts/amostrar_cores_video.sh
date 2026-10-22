#!/bin/sh
# Amostra as cores do slide de abertura do curso.
ARQ=/tmp/quadros/E1_00-10-00.jpg
FF="ffmpeg -v error -i $ARQ"

amostra() {
  x="$1"; y="$2"; nome="$3"
  hex=$(ffmpeg -v error -i "$ARQ" -vf "crop=4:4:${x}:${y},scale=1:1" -f rawvideo -pix_fmt rgb24 - 2>/dev/null | od -An -tu1 | tr -s ' ' | tr -d '\n' | sed 's/^ //')
  r=$(echo "$hex" | cut -d' ' -f1); g=$(echo "$hex" | cut -d' ' -f2); b=$(echo "$hex" | cut -d' ' -f3)
  printf '%-34s #%02X%02X%02X  (%s,%s)\n' "$nome" "$r" "$g" "$b" "$x" "$y"
}

echo "--- slide de abertura (17º Curso) ---"
amostra 120 60   "gradiente - inicio (turquesa)"
amostra 1850 60  "gradiente - fim (azul)"
amostra 960 520  "gradiente - meio"
amostra 700 110  "texto subtitulo (escuro)"
amostra 300 740  "titulo do curso (branco)"
amostra 960 300  "emblema ESEM (branco)"
amostra 60 1040 "fundo onda (turquesa claro)"

echo "--- slide de conteudo (E1_00-45-00) ---"
ARQ=/tmp/quadros/E1_00-45-00.jpg
amostra 1000 220 "faixa titulo caso (azul royal)"
amostra 300 60  "fundo slide (turquesa)"
amostra 1500 60 "fundo slide (azul)"
