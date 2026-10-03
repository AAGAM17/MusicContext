# Your own library, with licensing

Goal: pick the right track from music you own, and be able to say why, including whether you may use it commercially.

## 1. Index it

```bash
musiccontext library scan ~/Music
musiccontext library stats
```

Each file is analysed once (tempo with half/double-time alternatives, beats, downbeats, key, loudness, spectral
ratios) and de-duplicated by content hash. Re-scans only re-analyse files that changed. Nothing leaves your machine.

## 2. Record licenses where you can prove them

Put a sidecar next to the file, `track.mp3.license.json`:

```json
{ "license": "CC0-1.0", "commercial_use": true, "attribution_required": false }
```

Only a sidecar (or a provider API) makes a license `verified`. A copyright string in an ID3 tag is kept as an
unverified claim.

## 3. Ask for a recommendation

```bash
musiccontext recommend demo.mp4 --marker 11.0=reveal --commercial-use
```

Every candidate is scored on nine dimensions, each with a level, a basis and reasons:

```
1. click120  local  120 BPM [estimated]
   tempo_match           high    BPM 120.2 sits inside the 110-130 target range
   transition_fit        high    3/3 video cuts (100%) would fall on a beat at 120.2 BPM
   licensing_fit         high    verified by sidecar click120.wav.license.json
   mood_match            low     no tag or metadata value matches curious, minimal, ...
   energy_match          low     mean energy 0.65 against the direction's target 0.31
```

A track that cannot be verified for commercial use is **excluded with a reason** when you pass `--commercial-use`,
and a dimension MusicContext cannot measure is `unknown` and left out of the average rather than counted as zero.

The planned tempo range is a filter, so if nothing matches you are told what was searched for. Steer it with
`--bpm`; for example `--bpm 120` produced a 110–130 BPM target range in the run above.

## 4. Render it

```bash
musiccontext sync --video demo.mp4 --music ~/Music/track.mp3 --output final.mp4 --marker 11.0=reveal
```

The result reports where the track's beats landed against the video's cuts and your marker, in milliseconds, each
tagged `[estimated]`, because an offset is only as good as the beat estimate behind it.
