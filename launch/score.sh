#!/usr/bin/env bash
# Score the launch film with MusicContext itself: plan -> generate -> sync. Offline; no credentials.
#   bash launch/score.sh            (needs launch/film_silent.mp4 from make_video.py)
set -euo pipefail
cd "$(dirname "$0")/.."
VIDEO=launch/film_silent.mp4
# What we know from the storyboard: the title lands at 12.0 s, the proof at 24.0 s, the call to action at 28.0 s.
# 120 BPM puts every 4.0 s cut on a beat and every one of those moments on a downbeat.
ARGS=(--marker 12.0=reveal --marker 24.0=feature_reveal --marker 28.0=cta
      --platform cinematic --mood confident --mood curious --vocals none --bpm 120)

musiccontext plan     "$VIDEO" "${ARGS[@]}" --output launch/film.musicctx --force
musiccontext generate "$VIDEO" "${ARGS[@]}" --seed 7 --dest launch/film_score.wav --force
musiccontext sync --video "$VIDEO" --music launch/film_score.wav --output launch/film_scored.mp4 \
                  --target-lufs -16 --force "${ARGS[@]}"
