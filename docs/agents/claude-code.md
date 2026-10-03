# Using MusicContext from Claude Code

Two pieces work together:

- the **Skill** (`skills/musiccontext/`) teaches Claude *when* to use MusicContext, how to carry the
  `detected / estimated / inferred / user` distinction through to you, and what never to claim (a track is not
  "licensed" unless its license is verified);
- the **MCP server** (`musiccontext mcp`) gives Claude ten typed tools. See [mcp.md](mcp.md).

## Install

```bash
musiccontext init-agent --mcp
```

- The Skill is copied to `~/.claude/skills/musiccontext/`. Add `--project` to install into `./.claude/` instead.
- `--mcp` merges a `musiccontext` entry into `./.mcp.json` (an existing file is merged, never clobbered).
- Nothing is overwritten without `--force`. `--dry-run` prints exactly what would change.
- Restart your Claude Code session afterwards.

Check it:

```bash
musiccontext doctor      # "mcp server  responds to initialize and lists 10 tools"
```

## Use it

```
/musiccontext
Analyze demo.mp4 and tell me what soundtrack it needs.
```

Natural language works too: "use MusicContext to choose music for demo.mp4", "make the music peak at the product
reveal", "find music that works under the narration".

Tell it what you already know. A told fact beats an inference:

```
The reveal is at 18.2 seconds and the narration is in demo.srt.
```

Claude will pass `--marker 18.2=reveal` and the transcript, and the plan will mark that moment `[user]`.

## What to expect

- Claude reports which facts were **measured** (duration, cuts, loudness) and which were **estimated or inferred**
  (speech timing without a transcript, scene roles, the reveal, tempo and beats of a track).
- Rendering never overwrites your video: `sync` writes a new file and refuses an existing path without `force`.
- The MCP server only reads and writes inside the working directory. To allow another folder:
  `MUSICCONTEXT_ROOTS=/path/one:/path/two`.

## If the tools do not appear

1. `musiccontext` must be on the `PATH` that Claude Code was launched with. `which musiccontext`.
   A failure like `Executable not found in $PATH: musiccontext` means exactly that. Install with `pipx`, or put the
   virtualenv's `bin/` on the `PATH`, or put the absolute path in `.mcp.json` under `command`.
2. Run `musiccontext doctor` for the MCP self-test.
3. Restart the session. MCP servers load at startup.
