# Narrated product demo, offline

Goal: a soundtrack for a video with narration whose product reveal is at a known second. No music, no network, no
credentials.

```bash
make fixtures                      # or use your own video
cp tests/_fixtures/demo.mp4 .
```

## 1. Plan, and say what you know

```bash
musiccontext plan demo.mp4 --marker 11.0=reveal --platform product-demo --vocals none
```

You get the style, mood, a tempo range, an energy arc whose peak sits on 11.0 s, and a ducking policy. The reveal
is tagged `[user]` because you told us. The speech is tagged `[estimated]` because it came from a heuristic voice
detector.

Make speech `detected` by giving it timings (the text is discarded, only timings are kept):

```bash
musiccontext plan demo.mp4 --marker 11.0=reveal --transcript demo.srt
```

## 2. Generate a track that follows the plan

```bash
musiccontext generate demo.mp4 --marker 11.0=reveal --platform product-demo --vocals none --seed 7 --dest track.wav
```

Then read what was *measured*, not what was requested:

```
bpm      requested 91.1, measured 90.8   [estimated]
peaks    requested 11.0, nearest measured downbeat 11.003, offset 3.0 ms
duration requested 24.0, measured 23.917
license  generated-output, commercial use yes, verified: generated locally
```

The same seed gives the same track. The generator is a numpy synthesizer. It follows tempo, key, energy curve and
peak time faithfully, but it is a synthesizer and not a composer. Treat it as a reference bed, or hand the plan's
`brief` / `generation_prompt` to a human or a generation service.

## 3. Mix it in

```bash
musiccontext sync --video demo.mp4 --music track.wav --output final.mp4 --marker 11.0=reveal
```

```
Rendered final.mp4   duration 24.00s (video 24.00s)   ducking applied
  - fade in 0.5s at 0s / fade out 1.5s at 22.5s
  - music ducked 12.0 dB during 1.8-8.0s and 12.8-19.3s
  - original audio kept at full level
Sync points   11.0s reveal ↔ lift  +3.0 ms [estimated]
```

`final.mp4` is a new file. The input is never modified, and an existing output needs `--force`. Add `--dry-run` to
see the plan and FFmpeg command without rendering.

## One step

```bash
musiccontext render demo.mp4 --generate --seed 7 --marker 11.0=reveal -o final.mp4
```

## Why a told fact matters

Without `--marker`, the reveal and call-to-action are inferred from pacing and energy only. There is no vision model.
If a moment matters to the edit, say so.
