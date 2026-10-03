# Codex: status

**Codex support is unverified.** Nothing here has been confirmed against a live Codex session, and we will not
claim support until it is.

## What was checked

- Codex CLI available during development: `codex-cli 0.46.0`.
- `codex --help` lists `mcp` (management of MCP servers, marked experimental) and no skills command. The
  Skill-discovery path therefore could not be tested.
- The MusicContext MCP server itself was exercised over stdio with a hand-written JSON-RPC client:
  `initialize`, `tools/call musiccontext_inspect` on a local file (works), and the same tool on a path outside the
  sandbox (refused with `permission_denied`). That proves the server speaks the protocol. It does not prove that
  Codex calls it correctly.
- `musiccontext doctor` runs an in-process MCP self-test on every install.

## What is shipped

- `.codex-plugin/plugin.json`: a manifest, never loaded by a Codex release.
- `.codex/skills/musiccontext/` and `musiccontext init-agent --codex`: copy the Skill to `~/.codex/skills/`. Whether
  Codex reads that directory is exactly what is unverified.

## The supported route

Use the MCP server and the CLI. Add the server yourself (MusicContext does not edit your global Codex config):

```bash
codex mcp add musiccontext -- musiccontext mcp
codex mcp list
```

If you try it, please open an issue with your `codex --version`, what you ran and what happened. Either result,
working or not, is useful, and it is how this page gets upgraded from "unverified".
