# Roadmap

MusicContext wants to be the music-direction layer AI agents reach for. That means depth in reasoning and
honesty about uncertainty, not a longer feature list.

## Now (0.1.x)

- Harden what exists: more real-world media shapes (vertical video, long-form, variable frame rate, multi-track audio).
- Verify Codex end to end and document the result (`docs/agents/codex.md`).
- Tighten the heuristics with a labelled evaluation set instead of intuition.

## Next (0.2)

- **Better speech handling.** Optional local transcription (opt-in, local model, no upload) so voiceover timing is
  `detected` rather than `estimated` without asking the user for a transcript.
- **Per-section music.** Multiple cues per video with planned transitions between them, rather than one track.
- **Optional vision assist.** A pluggable visual analyzer for shot type, text/logo appearance and product moments,
  behind the same provider-style interface, with graceful degradation when absent. Reveals become `detected`.
- **Stem-aware mixing.** When a provider supplies stems, duck and thin arrangements per stem instead of per track.
- **Agent evaluation suite.** A scored benchmark for tool choice, timestamp accuracy, licensing correctness and
  injection resistance, run in CI.

## Later

- **Real provider integrations**, contributed behind the existing interfaces, each with its own contract tests and a
  documented data-handling statement.
- **Beat-accurate conform.** Nudging cuts to the music (an edit-side change) rather than only music to cuts, exposed
  as an explicit opt-in because it alters the user's edit.
- **REST service mode** with the same sandbox and bounded output as the MCP server, for queue-based deployments.
- **DAW/NLE handoff.** Export markers and cues as a format an editor can open (e.g. marker lists, AAF/OTIO
  investigation).
- **Loudness targets per platform**, verified against each platform's published normalization behaviour.

## Explicit non-goals

- Becoming a video editor, a DAW or a streaming service.
- Shipping a bundled music catalogue or a generation model in this package.
- Telemetry, or any default that sends media off the machine.
- Guessing. If MusicContext cannot measure something, it will keep saying so.

Have a use case that does not fit? Open an issue describing the video and the musical decision you need made.
