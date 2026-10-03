# The pipeline

```
video → story → music direction → soundtrack → sync → final video
```

CLI, MCP server and Python SDK all call one operation layer (`src/musiccontext/agent/ops.py`), so behaviour exists
exactly once. Every op takes an `OpContext` (settings plus a path policy) and returns typed objects plus a bounded,
JSON-safe dict. Nothing in that layer prints, and nothing processes media that was not explicitly asked for.

## 1. Analyze (`musiccontext analyze`)

`engine/pipeline.py: analyze_video`. Steps, with the timing of each recorded in the report:

1. **Probe**: container facts via `ffprobe` (`analysis/video.py`). Duration, resolution, fps and streams are `detected`.
2. **Cuts and audio in parallel**: FFmpeg scene scores give cuts (`analysis/scenes.py`, `detected`); the audio track is
   decoded to a short-time series (`analysis/audio.py`).
3. **Frames**: 64×36 frames are sampled for motion, brightness and colour. Cut spikes are removed so a hard cut does
   not read as "high motion" (`estimated`).
4. **Speech** (`analysis/speech.py`), first match wins:
   - a transcript you pass (`--transcript`): timings are `detected`, **text is discarded**, only timings and word counts are kept;
   - embedded subtitle streams: same treatment;
   - otherwise a heuristic voice-activity detector: `estimated`, confidence capped at 0.6.
5. **Story** (`analysis/story.py`): scenes become story segments with roles (`establishing`, `reveal`, `climax`,
   `resolution_cta`, …) and a ~1 Hz energy curve. Roles are `inferred` from pacing and energy only. There is no
   vision model and no OCR.

Results are cached by content hash plus tool version, analysis version and settings. A cached entry stores its own
key material and is re-verified on read.

## 2. Plan (`musiccontext plan`)

`direction/planner.py` turns the analysis plus your preferences into a `MusicDirection`:

| Part | Module | Notes |
|---|---|---|
| style, mood | `mood.py`, `presets.py` | platform presets (`product-demo`, `tiktok`, `documentary`, …) and brand profiles bias the result |
| energy and arc | `energy.py`, `arc.py` | a user reveal marker moves the peak; otherwise the inferred peak is used |
| tempo | `tempo.py` | capped near 108 BPM under heavy narration; prefers a BPM where the cut interval is a whole number of beats |
| instrumentation | `instrumentation.py` | preferred and prohibited instruments, vocals policy |
| constraints | `constraints.py` | duration, license requirements, prohibited tags |

Markers (`timeline/markers.py`) are typed moments (`reveal`, `cta`, `voiceover_start`, `ending`, …) each with an
importance and a suggested music action. A marker you pass with `--marker 18.2=reveal` has `source: user` and wins
over any inferred one.

The plan carries an `explanation` with evidence and a confidence value, and is saved as a `.musicctx` artifact.

## 3. Find or generate music

- **Library** (`music/library.py`): a SQLite index of your own files. Each file is analysed once (tempo with half/double
  alternatives, beats, downbeats, key, EBU R128 loudness, spectral ratios, energy lifts), de-duplicated by content hash.
- **Search** (`music/search.py`): queries the library plus any configured search providers and merges the results. One
  failing provider costs a warning, not the search.
- **Selection** (`music/selection.py`): each candidate is scored on nine independent dimensions (mood, tempo, energy,
  instrumentation, structure, voiceover compatibility, duration, transition, licensing). Unknown dimensions are
  *skipped, not zeroed*. Hard exclusions (for example, no verified commercial license when `--commercial-use` is
  set) are reported with a reason instead of silently dropped.
- **Generation** (`music/generation.py`, `providers/generation.py`): the built-in `procedural` provider synthesizes a
  track offline, deterministically from its seed, following the plan's tempo, key, energy curve and peak times. The
  result is then *measured* with the same analyzer used on any other track, so the reported BPM is observed.

## 4. Sync (`musiccontext sync`)

`sync/` builds one FFmpeg command from the plan (argument list, never a shell):

- trim, and loop on musical boundaries with crossfades (`looping.py`);
- fades (`fades.py`);
- speech ducking from the known speech intervals (`ducking.py`). The default is a time-based expression: exact and
  reproducible. `--ducking-mode sidechain` is level-driven instead;
- beat, downbeat and phrase alignment, with the achieved offset in milliseconds (`alignment.py`);
- video is stream-copied unless you pass `--reencode-video`.

The render goes to a temporary file in the destination directory, is moved into place atomically, and is then
**re-probed**: the reported duration is measured from the finished file, and a mismatch is a warning.

## Where the numbers come from

Every temporal value has a `source` (`detected`, `estimated`, `inferred`, `user`, `provider`), a confidence and a
provenance string. See [the data model](data-model.md). The README's table lists the honest limits of each estimator.
