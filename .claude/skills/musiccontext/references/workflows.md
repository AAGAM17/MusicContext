# Workflows

Ten scenarios end to end. Every command is real. `--json` is assumed for agent use; drop it for
human output. Nothing overwrites a path you pass unless you add `--force`.

Before the first run of any scenario: `musiccontext doctor` confirms FFmpeg is present, and
`musiccontext providers` says what music sources exist. If no library is scanned and no remote
provider is configured, your only music source is the offline `procedural` generator.

---

## 1. Product demo (screen recording with narration)

```bash
musiccontext inspect demo.mp4 --json
musiccontext plan demo.mp4 --json \
  --platform product-demo --transcript demo.srt --marker 18.2=feature_reveal
musiccontext recommend demo.mp4 --json --commercial-use
musiccontext sync --video demo.mp4 --music picked.wav --output demo-scored.mp4 --json
```

Tell the user: the preset holds the tempo in the 90-125 BPM band, forces instrumental, and leaves
1-4 kHz open for narration. Because a transcript was supplied, the voiceover timing is *detected*,
so the ducking blocks are exact. The 18.2s feature reveal is a *user* marker, so the arc's build and
peak are anchored to it rather than inferred.

Ask first: where is the feature reveal? Do not guess it.

---

## 2. Cinematic trailer

```bash
musiccontext plan trailer.mp4 --json --platform cinematic --variants 3 \
  --marker 0:12.0=reveal --marker 0:27.5=logo
musiccontext generate trailer.mp4 --json --duration 30 --output trailer.musicctx
```

Tell the user: the `cinematic` preset widens dynamics and biases to a hybrid/orchestral palette;
`--variants 3` returns a primary direction plus two alternatives whose top style differs. Read
`music_direction.brief` out loud — it is the brief you would hand a composer. If they want a real
composition, say the offline generator is a synthesizer and the brief is the deliverable for a human
or a configured provider.

---

## 3. SaaS launch video

```bash
musiccontext profile create acme --style technology --style minimal \
  --mood confident --avoid heavy_edm --instruments plucks --instruments sub_bass
musiccontext plan launch.mp4 --json --profile acme --platform advertisement \
  --marker 9.0=product_appearance --marker 21.4=cta --commercial-use
musiccontext recommend launch.mp4 --json --commercial-use --limit 5
```

Tell the user: `--commercial-use` makes verified licensing a **hard** requirement, so any track
whose license is not verified by a sidecar or provider API is pushed into
`excluded_candidates` with the reason. If the list comes back empty, that is the honest answer —
report it and offer `library scan` or offline generation rather than relaxing the requirement
silently.

---

## 4. LinkedIn video

```bash
musiccontext plan linkedin.mp4 --json --platform linkedin --transcript vo.vtt
musiccontext sync --video linkedin.mp4 --music bed.wav --output linkedin-final.mp4 \
  --json --target-lufs -16
```

Tell the user: the `linkedin` preset lowers energy, prefers `corporate`/`minimal`, forces
instrumental and targets -16 LUFS, because the video is often watched muted or quiet. Mention that
the music is deliberately unobtrusive — that is the preset doing its job, not a weak direction.

---

## 5. YouTube explainer

```bash
musiccontext analyze explainer.mp4 --json --transcript explainer.srt
musiccontext plan explainer.mp4 --json --platform youtube --transcript explainer.srt \
  --vocals none --energy 0.35
musiccontext sync --video explainer.mp4 --music bed.wav --output explainer-final.mp4 \
  --json --loop --target-lufs -14
```

Tell the user: with speech over most of the timeline the engine caps the tempo near 108 BPM so the
rhythm does not fight the narration, thins the instrumentation and excludes vocals even if a preset
allowed them. `--loop` repeats a short bed to cover the video; the result's `applied` list says how
many repeats and whether a crossfade was used.

---

## 6. Voiceover documentary

```bash
musiccontext plan doc.mp4 --json --platform documentary --transcript doc.srt \
  --marker 0:04.0=voiceover_start --marker 2:31.0=cta
musiccontext recommend doc.mp4 --json --limit 8
```

Tell the user: the `documentary` preset sits at 60-105 BPM, supportive and textural, rarely
beat-driven. The direction's `ducking` block will state the depth (8-12 dB depending on speech
density) and the frequency guidance. If no transcript exists, say plainly that voiceover intervals
are a heuristic estimate with confidence capped at 0.6, and that exporting subtitles would make them
exact.

---

## 7. Social reel (vertical short-form)

```bash
musiccontext plan reel.mp4 --json --platform reels --bpm 120 --marker 1.0=hook
musiccontext generate reel.mp4 --json --duration 15 --seed 7
musiccontext sync --video reel.mp4 --music generated.wav --output reel-final.mp4 \
  --json --target-lufs -14
```

Tell the user: `reels`/`shorts`/`tiktok` raise the energy bias, narrow the tempo band (95-140 BPM
depending on the preset) and force a `fade_out` ending so the clip loops cleanly. `--bpm 120` sets
`tempo.basis` to `user` — the engine stops reasoning about tempo and does what it was told.
`--seed` makes the generated track reproducible.

---

## 8. Silent screen recording

```bash
musiccontext inspect capture.mp4 --json          # confirm has_audio: false
musiccontext plan capture.mp4 --json --platform tutorial \
  --marker 0:06.0=title --marker 0:33.0=feature_reveal
musiccontext generate capture.mp4 --json
musiccontext sync --video capture.mp4 --music generated.wav --output capture-final.mp4 --json
```

Tell the user: with no audio stream there is no speech to detect, so `ducking.enabled` is false and
`capabilities.speech` says `unavailable: video has no audio stream`. Scene roles come only from
motion, cut rate and colour — so user markers carry most of the weight here. The rendered output
will carry the music only, and the result's `applied` list says exactly that.

---

## 9. Replacing an existing soundtrack while preserving timing

```bash
musiccontext analyze current.mp4 --json          # read stats.existing_music_estimate
musiccontext plan current.mp4 --json --marker 0:14.8=reveal
musiccontext recommend current.mp4 --json --bpm 112
musiccontext sync --video current.mp4 --music replacement.wav \
  --output current-rescored.mp4 --json --force
```

Tell the user: `stats.existing_music_estimate` is an estimate of the old track's tempo from
autocorrelation on the mixed audio — it is a hint for matching, not a measurement of the music stem.
Warn them explicitly: `sync` mixes the music over the original audio by default, so if the old music
is still in that track they will hear both. The old soundtrack must be removed upstream, or the
original audio dropped. Only pass `--force` because they asked you to overwrite that file.

---

## 10. Generating from scratch, fully offline

```bash
MUSICCONTEXT_LOCAL_ONLY=1 musiccontext providers --json
musiccontext plan scratch.mp4 --json --local-only --marker 0:10.0=reveal
musiccontext generate scratch.mp4 --json --local-only --provider procedural \
  --duration 24 --seed 1
musiccontext sync --video scratch.mp4 --music generated.wav --output scratch-final.mp4 --json
```

Tell the user: nothing left the machine. The `procedural` provider synthesizes from numpy with no
samples, no model weights and no third-party audio — which is the only reason its output can carry a
verified commercial-use license (`verified_by: generated locally by MusicContext`). It honours
duration, BPM, key, the energy curve and the peak times, and its metadata reports the requested
peak times, the peaks it actually landed on the beat grid, and the offset in milliseconds between
them. Quote those offsets rather than claiming perfect alignment. It is instrumental only; if
vocals were requested the metadata says so.

---

## Pattern: what to say at each step

| After | Say |
|---|---|
| `inspect` | duration, resolution, whether there is audio or subtitles |
| `analyze` | pace, cut rate, speech ratio **and `speech_source`**, where the inferred peak is |
| `plan` | style/mood/BPM with its basis, the arc with one-decimal times, the confidence note, every `messages[]` entry |
| `recommend` | the top candidate's reasons **and concerns**, license verification status, why others were excluded |
| `generate` | provider, duration, seed, requested vs landed peak offsets, license basis |
| `sync` | measured output duration vs expected, what was applied, whether ducking ran, real sync offsets |
