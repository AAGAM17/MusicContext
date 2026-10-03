# The data model and the `.musicctx` artifact

All schemas are pydantic v2 models in `src/musiccontext/schemas/`. They use `extra="forbid"`, so a misspelled field
is an error rather than silently ignored.

## Provenance on every value

Every temporal object extends `Temporal` (`schemas/timeline.py`):

| Field | Meaning |
|---|---|
| `start_time`, `end_time` | seconds; `end_time >= start_time` is enforced |
| `confidence` | 0–1 |
| `source` | `detected`, `estimated`, `inferred`, `user` or `provider` |
| `provenance` | which analyzer, rule or file produced it (max 300 chars) |

- `detected`: measured from the media, or supplied by a trusted parser.
- `estimated`: a heuristic with a known error margin.
- `inferred`: a judgement derived from other values ("this looks like a reveal").
- `user`: supplied by you or the calling agent.
- `provider`: reported by a music provider; not verified by MusicContext.

## The artifact

`musiccontext plan` writes `<home>/artifacts/<video>-<hash8>.musicctx` (override with `--output`). It is
human-readable JSON with `format: "musiccontext.artifact"` and a `version` (currently `1.0`). A different major
version is refused with a "re-run `musiccontext plan`" hint. Existing files are never overwritten without `--force`.

Top-level fields of `MusicContextResult` (`schemas/result.py`):

| Field | Contents |
|---|---|
| `tool_version`, `created_at` | what wrote it, and when |
| `project` | video name and `VideoMetadata` (duration, fps, resolution, streams, sanitized container tags) |
| `preferences` | what you asked for: styles, moods, BPM, vocals, platform, markers, license requirements |
| `analysis` | `AnalysisReport`: scenes, transitions, visual and audio events, speech, `StoryModel`, stats, capabilities, timings, warnings |
| `music_direction` | the plan: style, mood, energy, `tempo` (target and range), rhythm, instrumentation, texture, vocals, `arc`, `ducking`, constraints, requirement, explanation, human `brief`, provider-neutral `generation_prompt` |
| `alternatives` | other directions when you passed `--variants N` |
| `markers` | typed moments with importance and suggested music action |
| `timeline` | merged chronological view of markers, speech and arc sections |
| `sync_opportunities` | where music and picture can line up, with a reason |
| `candidates`, `excluded_candidates` | scored tracks (nine dimensions each), and exclusions with reasons |
| `generated` | the generated track and its *measured* features, if any |
| `selected`, `sync_decisions` | the chosen track and its placement (trim, loop, fades, ducking, alignment offsets) |
| `licenses` | license records, each marked verified or not, and by whom |
| `provider_options`, `provenance`, `warnings`, `messages` | what was available, where data came from, and actionable next steps |

## Reading it

```bash
musiccontext export demo.mp4           # summary
musiccontext export demo.mp4 --full    # everything
musiccontext export path/to/x.musicctx --json
```

```python
from musiccontext.engine.artifacts import load_artifact
r = load_artifact("demo.musicctx")
print(r.music_direction.brief)
```

## Scoring dimensions

`ScoreDimension.level` is `high`, `medium`, `low`, `unknown` or `not_applicable`, with an optional 0–1 `score` and a
`basis` (`detected`, `estimated`, `inferred`, `provider`, `unknown`). `unknown` means MusicContext could not measure
it, and it is excluded from the weighted mean instead of counting as zero.
