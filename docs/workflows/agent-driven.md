# Letting an agent drive it

Goal: ask Claude Code for a soundtrack and get an answer that separates what was measured from what was guessed.

```bash
musiccontext init-agent --mcp        # once; then restart the session
```

## A prompt

```
Use MusicContext to analyze demo.mp4. The product reveal is at 11.0 seconds.
Tell me in at most 4 lines what soundtrack it needs, and say which facts were
measured and which were estimated.
```

What a correct answer looks like (the agent should follow the Skill, `skills/musiccontext/SKILL.md`):

- **Measured:** duration (24.0 s), resolution, frame rate, the hard cut at 17.0 s.
- **Told by you:** the reveal at 11.0 s.
- **Estimated or inferred:** narration timing (heuristic detector unless you supply a transcript), the CTA at 17.0 s,
  scene roles, and, for any track, its tempo and beats.
- A recommendation that follows from those, such as a minimal, instrumental bed around 91 BPM that ducks under
  narration and peaks at 11.0 s.

## Why this is safe to hand to an agent

- The MCP server only touches the working directory unless you widen it (`MUSICCONTEXT_ROOTS`).
- Rendering never overwrites a file without `force`, and never modifies its input.
- Subtitle and transcript *text* is discarded. Strings in video metadata that look like instructions are surfaced as
  warnings, never obeyed.
- The agent is told never to claim a track is licensed unless its license is verified.

See [the MCP reference](../agents/mcp.md) and [Claude Code](../agents/claude-code.md).
