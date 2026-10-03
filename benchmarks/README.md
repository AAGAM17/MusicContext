# Benchmarks

`bench.py` measures the real analysis pipeline through the Python API — not the CLI — so
the numbers reflect library work, not argument parsing or process startup.

## Running it

```bash
# from the repo root
PYTHONPATH=src .venv/bin/python benchmarks/bench.py --durations 30 60 300
PYTHONPATH=src .venv/bin/python benchmarks/bench.py --durations 30 --json   # machine-readable
make bench                                                                  # 30 s + 60 s
```

Requirements: FFmpeg and FFprobe on `PATH`, plus `numpy` and `pydantic`. No new
dependencies, no network access, no fixtures to download — each clip is synthesized with
FFmpeg's `lavfi` sources into a temporary directory and deleted when the run ends.

`--durations 30` finishes in roughly 10 s, which makes it cheap enough to smoke in CI.
`1800` is deliberately opt-in: synthesizing a 30-minute clip dominates the run.

## What the numbers mean

| Column | Meaning |
| --- | --- |
| `dur` | Requested clip length. The clip carries a synthetic speech-like audio bed so the audio and speech stages do real work. |
| `probe` | `ffprobe` metadata + container hash. |
| `scenes` | FFmpeg scene-score cut detection. |
| `frames` | Frame sampling plus motion/colour statistics. |
| `audio` | Decode to mono and build the 50 Hz RMS / voice-ratio series. |
| `speech` | Heuristic voice-activity detection over that series. |
| `cold` | Total `analyze_video` with an empty cache. |
| `x-rt` | Clip seconds processed per wall second (`duration / cold`). Higher is better. |
| `warm` | Total `analyze_video` on a cache hit — it still re-probes metadata, because the file path may have moved since the entry was written. |
| `plan` | `create_plan` (music direction), pure Python over the report. |
| `RSS` | Peak resident set size of the whole process. |

The per-step figures come from `AnalysisReport.timings_s`, which the pipeline records
itself, rather than from re-timing internals in the harness. Scene detection and audio
analysis run concurrently, so the steps add up to more than `cold`.

**Peak RSS:** `resource.getrusage(RUSAGE_SELF).ru_maxrss` returns **bytes on macOS** and
**kilobytes on Linux**. `bench.py` normalises both to MiB. It is a whole-process
high-water mark, so it includes the interpreter and NumPy, not just the pipeline.

## Measured results

Indicative, not a guarantee.

**This machine:** macOS 15.7.9, x86_64, CPython 3.14.6, FFmpeg 7.1, APFS SSD.

| Clip | probe | scenes | frames | audio | speech | cold | x-realtime | warm | plan | peak RSS |
| ---: | ----: | -----: | -----: | ----: | -----: | ---: | ---------: | ---: | ---: | -------: |
| 30 s | 0.29 s | 0.38 s | 0.41 s | 0.42 s | 0.06 s | 1.21 s | 25x | 0.39 s | 0.004 s | 68 MB |
| 60 s | 0.29 s | 0.65 s | 0.55 s | 0.69 s | 0.02 s | 1.55 s | 39x | 0.28 s | 0.005 s | 81 MB |
| 300 s | 0.35 s | 1.16 s | 1.04 s | 1.26 s | 0.14 s | 2.83 s | 106x | 0.31 s | 0.008 s | 154 MB |

Reading them: analysis is dominated by a fixed FFmpeg startup cost, so x-realtime
improves with clip length rather than degrading. Warm runs are flat at roughly 0.3 s
regardless of duration, because a cache hit only re-probes the container. Peak RSS grows
with duration (the audio series is held in memory) but stays well under a few hundred MB
for a five-minute clip.

Caveats:

- **Results depend on the FFmpeg build and on disk speed.** A different FFmpeg (hardware
  decoders, different filter implementations) or a slower disk moves these numbers
  substantially. Treat them as a shape, not a contract.
- The synthetic clips are 320x180 at 10 fps. Real 1080p footage decodes more slowly; the
  per-step *proportions* are the transferable part.
- Figures are a single run on a laptop with other processes active, not a median of
  repeated trials.
