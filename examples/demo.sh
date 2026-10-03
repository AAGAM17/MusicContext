#!/usr/bin/env bash
# Offline end-to-end demo: plan -> generate -> sync on a synthesized 24 s video.
# No network, no credentials, no media to download. Needs `musiccontext` and ffmpeg on PATH.
#
#   examples/demo.sh [output-dir]        (run from a checkout of the repository)
set -euo pipefail

OUT="${1:-demo-out}"
PYTHON="${PYTHON:-python3}"
mkdir -p "$OUT"

if [ ! -f "$OUT/demo.mp4" ]; then
  "$PYTHON" scripts/make_fixtures.py "$OUT"      # writes demo.mp4 (and click.wav) deterministically
fi

# The reveal is at 11.0 s and the narration is in examples/narration.srt: tell MusicContext what we know.
ARGS=(--marker 11.0=reveal --platform product-demo --vocals none)

musiccontext plan "$OUT/demo.mp4" "${ARGS[@]}" --transcript examples/narration.srt
musiccontext generate "$OUT/demo.mp4" "${ARGS[@]}" --seed 7 --dest "$OUT/track.wav" --force
musiccontext sync --video "$OUT/demo.mp4" --music "$OUT/track.wav" --output "$OUT/final.mp4" --force "${ARGS[@]}"

echo
echo "Done: $OUT/final.mp4  (the input $OUT/demo.mp4 is untouched)"
