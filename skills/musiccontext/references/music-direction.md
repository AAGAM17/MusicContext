# Reading and shaping a MusicDirection

A `MusicDirection` is the whole decision in one object. Everything in it is overrideable.

## Style

Fourteen styles. Each is a feature prototype (energy, pace, brightness, saturation, warmth) compared
against the video's measured statistics, with a voiceover adjustment and a preset bonus on top.

| Style | Leans | Texture | Typical instruments | Voiceover |
|---|---|---|---|---|
| `cinematic` | mid-high energy, darker | hybrid, spacious | strings, piano, sub_bass, cinematic_percussion, synth_pads | slightly penalised |
| `documentary` | low energy, slow | organic, atmospheric | piano, soft_strings, acoustic_guitar, light_percussion | favoured |
| `corporate` | mid energy, bright | dry, hybrid | piano, acoustic_guitar, light_percussion, plucks | favoured |
| `technology` | mid energy, cool | synthetic, dry | synth, plucks, subtle_percussion, sub_bass | slightly favoured |
| `luxury` | low energy, dark, desaturated | spacious, hybrid | piano, strings, soft_pads, sub_bass | slightly favoured |
| `emotional` | low-mid, warm | acoustic, organic | piano, strings, acoustic_guitar | neutral |
| `energetic` | high energy, fast | synthetic, hybrid | drums, bass, synth, claps | strongly penalised |
| `playful` | mid-high, bright, saturated | organic, dry | ukulele, marimba, claps, pizzicato_strings | slightly penalised |
| `suspenseful` | mid energy, very dark | atmospheric, spacious | low_strings, pulses, sub_bass, textures | slightly penalised |
| `ambient` | very low energy | atmospheric, spacious | pads, textures, soft_piano | most favoured |
| `minimal` | low energy, bright | dry, spacious | piano, soft_pads, subtle_percussion, plucks | most favoured |
| `orchestral` | mid-high energy | acoustic, spacious | strings, brass, timpani, woodwinds | penalised |
| `electronic` | high energy, cool | synthetic | synth, drum_machine, bass, arpeggios | penalised |
| `acoustic` | mid energy, warm, bright | acoustic, organic | acoustic_guitar, piano, light_percussion, strings | slightly favoured |

Two styles are blended when the runner-up scores within 0.06 of the winner. `--style` replaces the
ranking outright (first two values used; a `genre` preference feeds the same slot). `--avoid` removes
a style from the ranking. Synonyms are accepted and normalised: `film`/`epic`/`trailer`/`score`/
`dramatic` -> `cinematic`, `tech`/`digital`/`innovation` -> `technology`, `upbeat`/`driving`/
`powerful` -> `energetic`, `edm`/`techno`/`house` -> `electronic`, `folk`/`guitar` -> `acoustic`, and
so on.

## Mood

Eleven moods, ranked against energy, brightness, saturation and warmth: `calm`, `mysterious`,
`melancholic`, `hopeful`, `warm`, `curious`, `confident`, `premium`, `futuristic`, `tense`,
`exciting`. Up to three are chosen — user/profile/preset moods first, then the top derived ones.
`--mood` feeds this slot, as does a brand profile's mood and a `brand_personality` preference.
Synonyms: `inspiring`/`uplifting`/`optimistic` -> `hopeful`, `suspense`/`dark`/`thriller` -> `tense`,
`elegant`/`sophisticated`/`high-end` -> `premium`, `sci-fi`/`synthwave` -> `futuristic`,
`sad`/`wistful`/`somber` -> `melancholic`.

## Energy

`energy` is 0-1 — the mean of the target music energy curve, which is the video's visual energy
curve scaled, offset by the preset's `energy_bias`, and clipped to 0.02-0.98. `--energy` shifts the
whole curve so its mean matches what you asked for; it does not flatten the shape.

Rhythm is a direct function of energy (and whether speech sits over the section):

| Energy | Rhythm | Under speech |
|---|---|---|
| < 0.25 | `sparse` | `sparse` below 0.55, else `moderate` |
| 0.25-0.45 | `relaxed` | |
| 0.45-0.65 | `moderate` | |
| 0.65-0.82 | `driving` | |
| >= 0.82 | `dense` | |

## Tempo, and why speech caps it

`TempoSpec` carries `target_bpm`, `min_bpm`, `max_bpm`, `basis` and a `rationale` listing every
step taken. Order of reasoning:

1. Base: `70 + 75 * energy` BPM.
2. Visual pacing: `fast` +8, `slow` -8.
3. **Speech cap:** when the voiceover covers more than 40% of the timeline the ceiling drops to
   112 BPM and the target to at most 108 BPM, so the rhythmic grid does not fight the syllable rate
   of speech. This is the single most important constraint on a narrated video.
4. Platform preset range clamps it further.
5. Cut rhythm: when cuts recur regularly (median interval, IQR within 25% of the median, at least
   4 cuts) the tempo is nudged to a value that puts every cut on a beat.
6. Final band: `target * 0.88` to `target * 1.12`, floored at 55 and capped at 190.

`--bpm` sets `basis: "user"` and skips all of that, with a +/-8% band.

## Instrumentation, texture, vocals

`instrumentation` is three lists: `preferred` (user, then brand, then the top three style
instruments), `optional`, `prohibited`. When speech covers 15% or more of the timeline,
`vocals`, `lead_vocal`, `busy_lead_melody` and `distorted_guitar` are added to `prohibited`, and
`soft_pads`, `plucks`, `subtle_percussion`, `sub_bass` are offered as `optional`. `--avoid` feeds
`prohibited` directly.

`texture` comes from the chosen styles (`spacious` is appended when there is speech).

`vocals` is one of `none`, `allowed`, `preferred`, `spoken_word`. Precedence: `--vocals`, then the
brand profile, then the preset, else `none`. If speech covers 15%+ and the user did **not** ask for
vocals, `allowed`/`preferred` is forced down to `none`.

## The musical arc

`arc.sections[]` follow the story segments. Story roles map to music section labels:

| Story role | Section label | Action | Description |
|---|---|---|---|
| `hook` | `hook` | `hit` | immediate rhythmic hook to grab attention |
| `establishing` | `intro` | `hold` | atmospheric, sparse opening |
| `problem` | `undercurrent` | `sustain` | subtle pulse underneath; keep space for the message |
| `build` | `build` | `build` | rising layers and density leading to the payoff |
| `reveal` / `climax` | `peak` | `peak` | full arrangement; the main hit lands on the payoff |
| `development` | `sustain` | `sustain` | hold energy without escalating |
| `resolution_cta` | `resolution` | `resolve` | ease off into a clear resolution / CTA |
| `outro` | `outro` | `resolve` | release and close cleanly |

Each section has `start_time`, `end_time`, `energy`, `rhythm`, `action`, `description`, plus
`source` and `confidence`. A section whose description ends "thin out and stay out of the speech
range during narration" has more than 35% speech over it.

`arc.peak_time` is anchored to a **user** marker of type `reveal`, `product_appearance`,
`feature_reveal` or `action_peak` when one exists — the segment is split at that time, the part
before becomes a `build`, and the section gets `source: "user"`, `confidence: 0.9`. Otherwise it is
the story model's inferred peak with `source: "inferred"`, `confidence: 0.5`. Any other segment that
claimed the peak loses it: the user's marker is authoritative.

`arc.ending` is `resolve`, `fade_out`, `hard_stop` or `sustain`. Short-form presets (`shorts`,
`reels`, `tiktok`) force `fade_out`. `arc.energy_curve` is a breakpoint list: builds ramp up, peaks
land above level then settle to 90%, resolutions decay to 60%.

## Markers and their suggested action

`MusicMarker` is a point in time (`timestamp`, `type`, `importance` 0-1,
`suggested_music_action`, `reason`, `source`, `confidence`, `provenance`).

| Marker type | Default importance | Suggested action |
|---|---|---|
| `reveal` | 0.95 | `peak` |
| `product_appearance` | 0.85 | `lift` |
| `logo` | 0.75 | `hit` |
| `feature_reveal` | 0.7 | `hit` |
| `title` | 0.7 | `hit` |
| `action_peak` | 0.65 | `hit` |
| `cta` | 0.6 | `resolve` |
| `ending` | 0.6 | `resolve` |
| `scene_change` | 0.55 | `transition_fill` |
| `voiceover_start` | 0.5 | `duck` |
| `voiceover_end` | 0.4 | `release` |
| `transition` | 0.35 | `transition_fill` |
| `hard_cut` | 0.3 | `none` |

Full action vocabulary: `none`, `hit`, `peak`, `lift`, `build`, `drop_out`, `resolve`, `duck`,
`release`, `fade_out`, `transition_fill`.

Where markers come from: hard cuts and soft transitions are `detected` (FFmpeg scene score, 0.45 is
the hard-cut cutoff). Story-section changes, reveals, climaxes and CTAs are `inferred`. Voiceover
start/end is `detected` with a transcript or subtitles, `estimated` from the heuristic VAD
otherwise. The end-of-video marker is `detected` from the container duration. A **user** marker
supersedes any inferred marker of the same type within 1.5s.

`sync_opportunities[]` turns the important markers into instructions with a `basis`: land the
biggest hit exactly here; introduce a lift on the nearest downbeat; place an accent on or just
before; resolve the phrase; start lowering ~0.2s before; bring the music back over ~0.5s; use a fill
to mask the transition. Plus a `cut_rhythm` row when the edit is regular enough to match a tempo.

## Ducking

`ducking` is only enabled when there is speech. It is time-based by default — derived from where the
speech is, not from how loud the narrator happens to be — so the same inputs always produce the same
output level.

| Field | Moderate speech (<= 60%) | Dense speech (> 60%) |
|---|---|---|
| `bed_gain_db` | -3.0 | -6.0 |
| `duck_depth_db` | 12.0 | 8.0 |
| `attack_ms` | 200 | 200 |
| `release_ms` | 500 | 700 |
| `lift_in_pauses` | true | false |

`energy_cap_under_speech` is 0.55. `frequency_guidance` says to keep 1-4 kHz uncluttered and favour
pads, sub-bass and plucks over lead melodies and busy hi-hats. Speech blocks are widened 0.2s before
and 0.3s after, merged when within 1.5s, and capped at 40 blocks so the filter string stays
parseable.

## Platform and content presets

Pass with `--platform NAME`. A preset biases; every field stays overrideable.

| Preset | Energy bias | BPM band | Styles | Moods | Vocals | LUFS | Note |
|---|---|---|---|---|---|---|---|
| `youtube` | 0.0 | — | — | — | — | -14 | Long-form: avoid fatigue, keep a bed under narration. |
| `shorts` | +0.12 | 100-140 | energetic | — | — | -14 | Hook in the first second, loop-friendly ending. |
| `reels` | +0.10 | 95-135 | energetic, electronic | — | — | -14 | Punchy, trend-adjacent, loop-friendly. |
| `tiktok` | +0.12 | 100-140 | energetic, electronic | — | — | -14 | Immediate hook, strong rhythm. |
| `linkedin` | -0.08 | — | corporate, minimal | confident | none | -16 | Often watched muted; professional and unobtrusive. Avoids heavy_edm. |
| `product-demo` | -0.03 | 90-125 | technology, minimal | confident, premium | none | -16 | Room for narration/UI sounds; build to the feature reveal. |
| `advertisement` | +0.05 | — | energetic, cinematic | confident, exciting | — | -14 | Clear arc with a decisive peak and clean ending. |
| `documentary` | -0.12 | 60-105 | documentary, ambient | curious | none | -18 | Supportive, textural, rarely beat-driven. |
| `presentation` | -0.15 | 70-105 | minimal, corporate | calm, confident | none | -18 | Stays out of the way of the speaker. |
| `game-trailer` | +0.15 | 80-150 | cinematic, orchestral | tense, exciting | — | -14 | Big dynamic range, hits on cuts. |
| `cinematic` | 0.0 | — | cinematic | premium | — | -16 | Wide dynamics, hybrid/orchestral palette. |
| `tutorial` | -0.15 | 70-105 | minimal, ambient | calm | none | -20 | Quiet bed; never competes with instruction. |

The LUFS column is the preset's recommendation. Apply it yourself with `sync --target-lufs`.

## Constraints and the requirement

`constraints[]` records each decision with a `severity` (`must`/`should`) and a `source` (`user`,
`preset`, `brand_profile`, `analysis`, `safety`): instrumental-only, tempo range, duration coverage,
clean ending, licensing, the avoid list, and which preset was applied. Without `--commercial-use`
there is always a `safety` constraint saying to check license metadata before publishing.

`requirement` is the machine-checkable version used to filter candidates: `bpm_range`, `vocals`,
`min_duration` (60% of the video, or 60% of `--duration`), `commercial_use_required`,
`license_must_be_verified`, `prohibited_tags`, `required_tags`.

## Confidence

`explanation.confidence` starts at 0.45 and is **capped at 0.85** — the analysis is heuristic with no
vision model, so it is never allowed to look certain. User markers add 0.15, a transcript or
subtitles add 0.10, explicit style/mood preferences add 0.05, a video under 6 seconds subtracts 0.10.
`confidence_note` spells out each reason. Quote it.
