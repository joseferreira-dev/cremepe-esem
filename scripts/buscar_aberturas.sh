#!/bin/sh
# Procura os quadros de abertura de cada video.
for t in 00:00:05 00:00:30 00:02:00; do
  nome="E2_$(echo "$t" | tr ':' '-').jpg"
  ffmpeg -v error -y -ss "$t" -i /srv/media/hls/2/720p/index.m3u8 -frames:v 1 -q:v 3 "/tmp/quadros/$nome" 2>/dev/null
  echo "  $nome"
done
for t in 00:09:00 00:10:00 00:11:00; do
  nome="E1_$(echo "$t" | tr ':' '-').jpg"
  ffmpeg -v error -y -ss "$t" -i /srv/media/hls/1/720p/index.m3u8 -frames:v 1 -q:v 3 "/tmp/quadros/$nome" 2>/dev/null
  echo "  $nome"
done
