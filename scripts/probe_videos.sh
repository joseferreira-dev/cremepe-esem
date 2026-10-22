#!/bin/sh
for f in /a/*.mp4; do
    echo "=== $f ==="
    ls -lh "$f"
    ffprobe -v error -show_entries format=duration,bit_rate,size -show_entries stream=codec_type,codec_name,width,height,r_frame_rate,bit_rate -of default=noprint_wrappers=1 "$f"
done
df -h /srv 2>/dev/null || df -h /
