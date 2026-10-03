# The MCP server

```bash
musiccontext mcp                       # JSON-RPC 2.0, newline-delimited, over stdio
musiccontext mcp --roots /a:/b         # allow these directories (os.pathsep-separated)
```

Client config (Claude Code `.mcp.json`, Cursor, and most others use the same shape):

```json
{ "mcpServers": { "musiccontext": { "command": "musiccontext", "args": ["mcp"], "env": {} } } }
```

Protocol versions answered: `2024-11-05`, `2025-03-26`, `2025-06-18`. Logs go to stderr only, because stdout carries
protocol frames.

## Tools

| Tool | Does | Writes |
|---|---|---|
| `musiccontext_inspect` | container metadata; no analysis | no |
| `musiccontext_analyze` | scenes, cuts, motion, energy, speech, story | cache only |
| `musiccontext_plan` | the Music Direction Plan, summarised by default | `.musicctx` artifact |
| `musiccontext_recommend` | scored candidates with reasons, plus a placement for the best | artifact |
| `musiccontext_search` | providers plus your library, by text or by a video's direction | no |
| `musiccontext_library_search` | your local library only; never touches the network | no |
| `musiccontext_generate` | a track following the plan (offline `procedural` by default) | audio file |
| `musiccontext_sync` | the rendered video | new video (destructive hint set) |
| `musiccontext_providers` | what is available, what each needs, what leaves the machine | no |
| `musiccontext_get_artifact` | read a stored `.musicctx` | no |

Input schemas are strict (`additionalProperties: false`, with ranges and enums). Each tool carries MCP annotations
(`readOnlyHint`, `destructiveHint`, `idempotentHint`). `musiccontext_sync` never overwrites unless `force` is true and
never modifies its input.

## Stricter than the CLI

| Control | Behaviour |
|---|---|
| Filesystem sandbox | Only the working directory, plus `--roots` or `MUSICCONTEXT_ROOTS`. Traversal, NUL bytes and symlinks that escape are rejected. `/etc/passwd` returns `permission_denied` with the allowed roots in the hint. |
| Output bound | Every result is capped (`MUSICCONTEXT_MAX_OUTPUT_CHARS`, default 60000). |
| Sanitized text | Container tags, transcripts and provider fields are length-bounded; instruction-like strings produce a `warnings` entry. |
| Remote URLs | Off by default; see SECURITY.md. |
| Credentials | Never returned; `musiccontext_providers` reports names only. |
| Work on demand | Nothing is processed until a tool call asks for it. |

Errors come back as tool results with `error`, `hint` and `code`, so the agent can tell you what to do next.

## Try it by hand

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | musiccontext mcp
```
