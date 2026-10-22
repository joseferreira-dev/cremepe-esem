#!/bin/sh
# Compoe o emblema com alpha sobre um fundo escuro para conferir o recorte.
ffmpeg -v error -y -i /tmp/emblema-esem.png -f lavfi -i "color=c=0x00455F:s=440x440" \
  -filter_complex "[0:v][1:v]overlay" -frames:v 1 /tmp/emblema-teste.png
ls -la /tmp/emblema-teste.png
