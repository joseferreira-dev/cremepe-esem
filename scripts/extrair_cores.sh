#!/bin/sh
sample() {
    file="$1"; x="$2"; y="$3"; name="$4"
    hex=$(ffmpeg -v error -i "$file" -vf "crop=4:4:${x}:${y},scale=1:1" -f rawvideo -pix_fmt rgb24 - 2>/dev/null | od -An -tu1 | tr -s ' ' | tr -d '\n' | sed 's/^ //')
    r=$(echo "$hex" | cut -d' ' -f1)
    g=$(echo "$hex" | cut -d' ' -f2)
    b=$(echo "$hex" | cut -d' ' -f3)
    printf '%s (%s,%s) = #%02X%02X%02X\n' "$name" "$x" "$y" "$r" "$g" "$b"
}
echo "--- dimensoes ---"
ffprobe -v error -show_entries stream=width,height -of csv=p=0 /a/banner.jpg
ffprobe -v error -show_entries stream=width,height -of csv=p=0 /a/logotipo-cremepe.png
echo "--- banner ---"
sample /a/banner.jpg 60 1450 rodape_escuro
sample /a/banner.jpg 600 1450 rodape_meio
sample /a/banner.jpg 1090 1000 azul_fundo
sample /a/banner.jpg 1090 1400 azul_escuro
sample /a/banner.jpg 560 200 teal_texto
sample /a/banner.jpg 250 120 roxo_mandala
echo "--- logo ---"
sample /a/logotipo-cremepe.png 55 40 verde_anel
sample /a/logotipo-cremepe.png 130 30 verde_anel2
sample /a/logotipo-cremepe.png 900 120 cinza
