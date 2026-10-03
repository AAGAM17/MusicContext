# Examples

| File | What it is |
|---|---|
| `demo.sh` | the whole flow offline: synthesize a video, plan, generate a track, render `final.mp4` |
| `narration.srt` | a transcript. Pass it with `--transcript` to make narration timing `detected` instead of `estimated`. Only timings are used; the text is discarded. |
| `track.mp3.license.json` | a license sidecar. Name it `<audio file>.license.json` and put it next to the audio file; the license becomes `verified`. |

```bash
make install                           # once
PATH="$PWD/.venv/bin:$PATH" PYTHON=.venv/bin/python examples/demo.sh
```

Output lands in `demo-out/`. To use your own video, drop it in as `demo-out/demo.mp4` first.
