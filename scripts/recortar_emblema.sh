#!/bin/sh
# Metodo com alphamerge: gera a mascara em escala de cinza e funde como alpha.
SRC=/tmp/quadros/E1_00-10-00.jpg
L=${1:-150}

ffmpeg -v error -y -i "$SRC" -vf "crop=440:440:740:220" /tmp/emblem_bruto.png
ffmpeg -v error -y -i /tmp/emblem_bruto.png -vf "format=gray,lut=y='if(gt(val,$L),255,0)'" -frames:v 1 /tmp/mask.png
ffmpeg -v error -y -i /tmp/emblem_bruto.png -i /tmp/mask.png \
  -filter_complex "[0:v][1:v]alphamerge" -frames:v 1 /tmp/emblema-esem.png
ffmpeg -v error -y -i /tmp/emblema-esem.png -f lavfi -i "color=c=0x00455F:s=440x440" \
  -filter_complex "[0:v][1:v]overlay" -frames:v 1 /tmp/emblema-teste.png
ls -la /tmp/emblema-esem.png /tmp/emblema-teste.png
