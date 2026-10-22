#!/bin/sh
# Descobre o limiar adequado para o emblema e gera o recorte com transparencia.
SRC=/tmp/quadros/E1_00-10-00.jpg
ffmpeg -v error -y -i "$SRC" -vf "crop=440:440:740:220" /tmp/emblem_bruto.png

echo "--- brilho maximo na regiao do emblema ---"
ffmpeg -v error -i /tmp/emblem_bruto.png -vf "signalstats,metadata=print:key=lavfi.signalstats.YMAX" -f null - 2>&1 | head -3

for L in 120 140 150 160 170; do
  ffmpeg -v error -y -i /tmp/emblem_bruto.png \
    -vf "format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(gt(0.299*r(X,Y)+0.587*g(X,Y)+0.114*b(X,Y),$L),255,0)'" \
    -frames:v 1 "/tmp/emb_$L.png"
  ffmpeg -v error -y -i "/tmp/emb_$L.png" -f lavfi -i "color=c=0x00455F:s=440x440" \
    -filter_complex "[0:v][1:v]overlay" -frames:v 1 "/tmp/emb_${L}_fundo.png"
  echo "limiar $L -> $(ls -la /tmp/emb_${L}_fundo.png | awk '{print $5}') bytes"
done
