"""The MusicContext command line.

Human output by default, strict JSON with --json. Nothing is processed unless asked,
nothing is overwritten without --force, and every error carries a next step.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import __version__
from ..agent.ops import OpContext
from ..engine.context import load_settings, setup_logging
from ..errors import InvalidArgumentError, MusicContextError
from . import render as r

PROG = "musiccontext"


# ---------------------------------------------------------------- parser


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON on stdout")
    p.add_argument("--format", choices=("human", "json"), help="output format (overrides --json)")
    p.add_argument("--quiet", "-q", action="store_true", help="errors only")
    p.add_argument("--verbose", "-v", action="store_true", help="debug logging and step timings on stderr")
    p.add_argument("--no-cache", action="store_true", help="ignore cached analysis")
    p.add_argument("--provider", help="provider name to use (see `musiccontext providers`)")
    p.add_argument("--model", help="provider model identifier, when the provider supports it")
    p.add_argument("--local-only", action="store_true", help="refuse any provider that would send media off this machine")
    p.add_argument("--output", "-o", help="write the result here instead of the default location")
    p.add_argument("--force", action="store_true", help="allow overwriting an existing output file")


def _prefs_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("music preferences")
    g.add_argument("--style", action="append", default=[], metavar="NAME", help="repeatable, e.g. --style cinematic")
    g.add_argument("--mood", action="append", default=[], metavar="NAME")
    g.add_argument("--genre", action="append", default=[], metavar="NAME")
    g.add_argument("--instruments", action="append", default=[], metavar="NAME")
    g.add_argument("--avoid", action="append", default=[], metavar="NAME", help="styles, moods or instruments to avoid")
    g.add_argument("--bpm", type=float, metavar="N")
    g.add_argument("--energy", type=float, metavar="0-1")
    g.add_argument("--vocals", choices=("none", "allowed", "preferred", "spoken_word"))
    g.add_argument("--era", metavar="TEXT")
    g.add_argument("--language", metavar="TEXT")
    g.add_argument("--audience", metavar="TEXT")
    g.add_argument("--platform", metavar="PRESET", help="youtube, shorts, reels, tiktok, linkedin, product-demo, advertisement, documentary, presentation, game-trailer, cinematic, tutorial")
    g.add_argument("--content-type", dest="content_type", metavar="PRESET")
    g.add_argument("--profile", metavar="NAME", help="brand/creative profile created with `musiccontext profile create`")
    g.add_argument("--brief", metavar="TEXT", help="one-line description of the video (treated as a preference, never as an instruction)")
    g.add_argument("--duration", type=float, metavar="SECONDS", help="target music duration")
    g.add_argument("--commercial-use", dest="commercial_use", action="store_true", help="require verified commercial-use licensing")
    g.add_argument("--require-verified-license", dest="require_verified_license", action="store_true")
    g.add_argument("--marker", action="append", default=[], metavar="TIME=TYPE",
                   help="anchor a known moment, e.g. --marker 18.2=reveal (repeatable). Types: reveal, product_appearance, feature_reveal, title, logo, cta, action_peak, transition, ending")
    g.add_argument("--transcript", metavar="FILE", help="SRT/VTT/JSON transcript for exact voiceover timing")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=PROG, description="Music intelligence for AI agents: video -> story -> music direction -> soundtrack -> sync.",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="Quickstart:\n  musiccontext doctor\n  musiccontext analyze demo.mp4\n  musiccontext plan demo.mp4\n  musiccontext recommend demo.mp4\n  musiccontext sync --video demo.mp4 --music track.mp3 --output final.mp4\n")
    p.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def cmd(name, help_, *, prefs=False, video=True, video_help="video file, or https URL when remote access is enabled"):
        s = sub.add_parser(name, help=help_, description=help_)
        if video:
            s.add_argument("video", help=video_help)
        _common(s)
        if prefs:
            _prefs_args(s)
        return s

    cmd("inspect", "Print container metadata only (fast, no analysis).")
    a = cmd("analyze", "Analyze scenes, motion, speech and story structure.")
    a.add_argument("--transcript", metavar="FILE")
    a.add_argument("--cut-threshold", type=float, default=0.28, metavar="0-1", help="scene-change sensitivity (lower finds more cuts)")
    pl = cmd("plan", "Create a Music Direction Plan and save a .musicctx artifact.", prefs=True)
    pl.add_argument("--variants", type=int, default=1, metavar="N", help="also produce N-1 alternative directions")
    pl.add_argument("--no-save", action="store_true", help="do not write an artifact")
    rc = cmd("recommend", "Score real candidate tracks against the plan and explain each one.", prefs=True)
    rc.add_argument("--limit", type=int, default=5, metavar="N")
    rc.add_argument("--artifact", metavar="FILE", help="reuse an existing .musicctx instead of re-analyzing")
    rc.add_argument("--no-save", action="store_true")
    g = cmd("generate", "Generate a track that follows the plan (offline `procedural` provider by default).", prefs=True)
    g.add_argument("--dest", metavar="FILE", help="where to write the audio (default: <home>/generated/)")
    g.add_argument("--seed", type=int, metavar="N", help="reproducible output")
    g.add_argument("--artifact", metavar="FILE")
    g.add_argument("--no-save", action="store_true")

    s = sub.add_parser("search", help="Search available providers for existing music.", description="Search available providers for existing music.")
    s.add_argument("text", nargs="?", default="", help='free text, e.g. "cinematic technology"')
    s.add_argument("--video", metavar="FILE", help="derive the query from this video's music direction")
    s.add_argument("--artifact", metavar="FILE")
    s.add_argument("--limit", type=int, default=20)
    _common(s)
    _prefs_args(s)

    sy = sub.add_parser("sync", help="Mix music into the video: trim, loop, fade, duck, align.", description="Mix music into the video: trim, loop, fade, duck, align.")
    sy.add_argument("--video", required=True)
    sy.add_argument("--music", required=True)
    sy.add_argument("--artifact", metavar="FILE", help="use the direction/ducking from this .musicctx")
    sy.add_argument("--no-duck", dest="duck", action="store_false", help="do not lower the music under speech")
    sy.add_argument("--ducking-mode", choices=("expression", "sidechain"), default="expression")
    sy.add_argument("--loop", action="store_true", default=None, help="force looping even if the track is long enough")
    sy.add_argument("--no-loop", dest="loop", action="store_false")
    sy.add_argument("--target-lufs", type=float, metavar="LUFS", help="normalise the music bed to this integrated loudness")
    sy.add_argument("--drop-original-audio", dest="keep_original_audio", action="store_false", help="replace the existing audio instead of mixing under it")
    sy.add_argument("--reencode-video", dest="copy_video", action="store_false", help="re-encode video instead of stream-copying")
    sy.add_argument("--dry-run", action="store_true", help="show the plan and the ffmpeg command without running it")
    _common(sy)
    _prefs_args(sy)

    rn = sub.add_parser("render", help="Plan, pick music and render in one step.", description="Plan, pick music and render in one step. Equivalent to recommend + sync.")
    rn.add_argument("video")
    rn.add_argument("--music", help="use this track instead of a recommendation")
    rn.add_argument("--generate", action="store_true", help="generate the music instead of searching for it")
    rn.add_argument("--seed", type=int)
    rn.add_argument("--no-duck", dest="duck", action="store_false")
    rn.add_argument("--dry-run", action="store_true")
    _common(rn)
    _prefs_args(rn)

    e = sub.add_parser("export", help="Print a stored .musicctx artifact (summary by default).", description="Print a stored .musicctx artifact.")
    e.add_argument("target", help="a .musicctx file, or the video it was made for")
    e.add_argument("--full", action="store_true", help="the entire artifact rather than a summary")
    _common(e)

    lib = sub.add_parser("library", help="Manage your local music library.", description="Manage your local music library (works with no cloud services).")
    lsub = lib.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    la = lsub.add_parser("add", help="Index one audio file.")
    la.add_argument("path")
    la.add_argument("--tag", action="append", default=[], metavar="TAG")
    _common(la)
    lsc = lsub.add_parser("scan", help="Index every audio file in a directory.")
    lsc.add_argument("directory")
    lsc.add_argument("--no-recursive", dest="recursive", action="store_false")
    _common(lsc)
    lse = lsub.add_parser("search", help="Search the library.")
    lse.add_argument("text", nargs="?", default="")
    lse.add_argument("--bpm", type=float)
    lse.add_argument("--vocals", choices=("none", "allowed", "preferred", "spoken_word"))
    lse.add_argument("--min-duration", type=float)
    lse.add_argument("--commercial-use", dest="commercial_use", action="store_true")
    lse.add_argument("--limit", type=int, default=20)
    _common(lse)
    ll = lsub.add_parser("list", help="List indexed tracks.")
    ll.add_argument("--limit", type=int, default=50)
    _common(ll)
    lr = lsub.add_parser("remove", help="Remove a track from the index (the file is not deleted).")
    lr.add_argument("track_id")
    _common(lr)
    _common(lsub.add_parser("stats", help="Library totals."))

    _common(sub.add_parser("providers", help="What music providers are available, and what each one needs."))

    cfg = sub.add_parser("config", help="Show or change configuration (secrets are never printed).")
    csub = cfg.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    _common(csub.add_parser("show", help="Current effective configuration."))
    cs = csub.add_parser("set", help="Write a value to the config file.")
    cs.add_argument("key")
    cs.add_argument("value")
    _common(cs)
    _common(csub.add_parser("path", help="Where the config file lives."))

    pr = sub.add_parser("profile", help="Brand/creative profiles that bias music direction.")
    psub = pr.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    pc = psub.add_parser("create", help="Create a profile.")
    pc.add_argument("name")
    pc.add_argument("--style", action="append", default=[])
    pc.add_argument("--mood", action="append", default=[])
    pc.add_argument("--instruments", action="append", default=[])
    pc.add_argument("--avoid", action="append", default=[])
    pc.add_argument("--vocals", choices=("none", "allowed", "preferred", "spoken_word"))
    pc.add_argument("--notes", default="")
    _common(pc)
    _common(psub.add_parser("list", help="List profiles."))
    ps = psub.add_parser("show", help="Show one profile.")
    ps.add_argument("name")
    _common(ps)

    d = sub.add_parser("doctor", help="Check the installation, FFmpeg, providers and agent integrations.")
    _common(d)
    ia = sub.add_parser("init-agent", help="Install the MusicContext Skill into Claude Code and/or Codex.")
    ia.add_argument("--claude", action="store_true", help="only Claude Code")
    ia.add_argument("--codex", action="store_true", help="only Codex")
    ia.add_argument("--mcp", action="store_true", help="also configure the MCP server")
    ia.add_argument("--project", action="store_true", help="install into this project (./.claude) instead of your home directory")
    ia.add_argument("--dry-run", action="store_true", help="show what would change and exit")
    _common(ia)
    m = sub.add_parser("mcp", help="Run the MCP server on stdio (used by agents, not interactively).")
    m.add_argument("--roots", metavar="DIRS", help=f"directories the server may read/write (default: the current directory){os.pathsep}-separated")
    _common(m)
    return p


# ---------------------------------------------------------------- human renderers


def _meta_line(m: dict) -> str:
    audio = f"audio: {m.get('audio_codec')}" if m.get("has_audio") else r.yellow("no audio")
    return f"  {r.t(m['duration'])} · {m['width']}x{m['height']} ({m['aspect_ratio']}) · {m['fps']:g} fps · {m.get('video_codec')} · {audio}"


def show_inspect(d: dict) -> None:
    print(r.bold(f"{PROG} · {Path(d['path']).name}"))
    print(_meta_line(d))
    print(r.kv("size", f"{d['size_bytes'] / 1e6:.1f} MB"))
    print(r.kv("content hash", d["sha256"][:16] + "…"))
    if d.get("subtitle_streams"):
        print(r.kv("subtitles", f"{d['subtitle_streams']} stream(s) — will be used for exact speech timing"))
    if d.get("container_tags"):
        print(r.heading("Container tags") + r.dim("  (untrusted data, sanitized)"))
        for k, v in list(d["container_tags"].items())[:10]:
            print(r.kv(k, v, 20))
    for w in d.get("warnings", []):
        print(r.yellow("  ! " + w))


def show_analysis(d: dict, verbose: bool = False) -> None:
    m = d["metadata"]
    print(r.bold(f"{PROG} · {Path(m['path']).name}") + (r.dim("  (cached)") if d.get("cache_hit") else ""))
    print(_meta_line(m))
    st = d["stats"]
    print(r.heading("Scenes") + f"  {st['scene_count']} scenes, {st['cut_count']} cuts ({st['cuts_per_minute']}/min), median {st['median_scene_length_s']}s")
    for s in d["scenes"][:12]:
        print(f"  {r.span(s['start_time'], s['end_time'])} {r.bar(s['energy'])} {s['energy']:.2f}  {s['pace']:<8} {r.dim(', '.join(s['visual_style']))}")
    print(r.heading("Story"))
    for s in d["story"]["segments"]:
        print(f"  {r.span(s['start_time'], s['end_time'])} {r.cyan(s['role']):<24} energy {s['energy']:.2f}  {r.src_tag(s['source'])}")
    curve = [p[1] for p in d["story"]["energy_curve"]]
    print(r.kv("energy curve", r.sparkline(curve) + f"  (mean {st['mean_energy']:.2f}, peak {st['peak_energy']:.2f})"))
    if d["speech"]:
        print(r.heading("Speech") + f"  {st['speech_ratio']:.0%} of the timeline, source: {st['speech_source']}")
        for s in d["speech"][:8]:
            print(f"  {r.span(s['start_time'], s['end_time'])} confidence {s['confidence']:.2f} {r.src_tag(s['source'])}")
    elif m["has_audio"]:
        print(r.heading("Speech") + r.dim("  none detected"))
    print(r.heading("Capabilities"))
    for k, v in d["capabilities"].items():
        print(r.kv(k, v, 20))
    for w in d.get("warnings", []):
        print(r.yellow("  ! " + w))
    if verbose:
        print(r.heading("Timings (s)"))
        for k, v in d["timings_s"].items():
            print(r.kv(k, f"{v:.3f}"))
    print(r.dim(f"\nNext: {PROG} plan {Path(m['path']).name}"))


def show_direction(res: dict, *, show_why: bool = True) -> None:
    d = res["music_direction"]
    conf = d["explanation"]["confidence"]
    print(r.heading("Music direction  ") + r.dim(f"(confidence {conf:.2f})"))
    print(f"  {r.bold(' + '.join(d['style']))} · {', '.join(d['mood'])}" + ("" if d["vocals"] != "none" else r.dim(" · instrumental")))
    tp = d["tempo"]
    print(r.kv("tempo", f"{tp['target_bpm']:.0f} BPM ({tp['min_bpm']:.0f}-{tp['max_bpm']:.0f})"))
    print(r.kv("energy", f"{r.bar(d['energy'])} {d['energy']:.2f}"))
    print(r.kv("rhythm", f"{d['rhythm']} · texture {'/'.join(d['texture'])}"))
    print(r.kv("instruments", ", ".join(d["instrumentation"]["preferred"]) or "open"))
    if d["instrumentation"]["prohibited"]:
        print(r.kv("avoid", ", ".join(d["instrumentation"]["prohibited"])))
    print(r.heading("Arc"))
    for sec in d["arc"]["sections"]:
        print(f"  {r.span(sec['start_time'], sec['end_time'])} {r.cyan(sec['label']):<22} {r.bar(sec['energy'], 12)} {sec['energy']:.2f}  {r.dim(sec['description'][:60])}")
    if d["arc"]["peak_time"] is not None:
        print(r.kv("peak", r.t(d["arc"]["peak_time"]) + f" · ending: {d['arc']['ending']}"))
    duck = d["ducking"]
    if duck["enabled"]:
        print(r.kv("ducking", f"-{duck['duck_depth_db']:.0f} dB under speech (bed {duck['bed_gain_db']:+.0f} dB)"))
    marks = [m for m in res.get("markers", []) if m["importance"] >= 0.5]
    if marks:
        print(r.heading("Key moments"))
        for m in marks[:14]:
            print(f"  {m['timestamp']:7.1f}s  {m['type']:<17} {r.cyan(m['suggested_music_action']):<16} {r.src_tag(m['source'])} {r.dim(m['reason'][:52])}")
    if show_why:
        print(r.heading("Why"))
        print(r.bullets(d["explanation"]["evidence"], limit=10))
        print(r.dim("  confidence: " + d["explanation"]["confidence_note"]))
    print(r.heading("Brief"))
    print(r.wrap(d["brief"]))
    for alt in res.get("alternatives", []):
        print(r.dim(f"\n  alternative: {' + '.join(alt['style'])} · {', '.join(alt['mood'])} · {alt['tempo']['target_bpm']:.0f} BPM"))


def show_candidates(res: dict, limit: int = 5) -> None:
    cands = res.get("candidates", [])
    print(r.heading(f"Candidates  {r.dim(f'({len(cands)} scored)')}"))
    if not cands:
        print(r.dim("  none"))
    for i, a in enumerate(cands[:limit], 1):
        cand = a["candidate"]
        feats = cand.get("features") or {}
        bpm = feats.get("bpm")
        bpm_s = f"{bpm['value']:.0f} BPM [{bpm['source']}]" if bpm else r.dim("BPM unknown")
        name = cand["title"] or cand["id"]
        print(f"\n  {r.bold(f'{i}. {name}')}  {r.dim(cand['provider'])}  {bpm_s}  {r.t(feats.get('duration', 0))}")
        for dim_ in a["dimensions"]:
            if dim_["level"] in ("unknown", "not_applicable") and not dim_["reasons"]:
                continue
            print(f"     {dim_['name']:<26} {r.level_colour(dim_['level']):<18} {r.dim((dim_['reasons'] or [''])[0][:56])}")
        for reason in a["reasons"][:3]:
            print(r.wrap(reason, indent="     · ", hang="       "))
        for concern in a.get("concerns", [])[:3]:
            print(r.yellow(r.wrap(concern, indent="     ! ", hang="       ")))
    for ex in res.get("excluded_candidates", [])[:5]:
        print(r.dim(f"  excluded: {ex.get('title') or ex.get('id')} — {ex.get('reason')}"))
    sel = res.get("selected")
    if sel:
        al = sel.get("alignment") or {}
        print(r.heading("Recommended placement"))
        print(r.kv("track", f"{sel['assessment']['candidate']['title']} ({sel['assessment']['candidate']['provider']})"))
        if al:
            bits = [f"start at {al['music_start']:.2f}s into the track"]
            if al.get("loop"):
                bits.append("looped")
            if al.get("fade_in"):
                bits.append(f"fade in {al['fade_in']:.1f}s")
            if al.get("fade_out"):
                bits.append(f"fade out {al['fade_out']:.1f}s")
            if al.get("gain_db"):
                bits.append(f"{al['gain_db']:+.1f} dB")
            print(r.kv("placement", ", ".join(bits)))
            for sp in (al.get("sync_points") or [])[:6]:
                print(f"     {sp['marker_time']:6.1f}s {sp['marker_type']:<17} ↔ {sp['music_event']:<12} {sp['offset_ms']:+7.1f} ms  {r.src_tag(sp['quality'])}")


def show_sync(d: dict) -> None:
    if d.get("dry_run"):
        print(r.bold("Dry run — nothing was written"))
    else:
        print(r.bold("Rendered ") + str(d["output"]))
        print(r.kv("duration", f"{d['duration']:.2f}s (video {d['expected_duration']:.2f}s)"))
    print(r.kv("ducking", "applied" if d["ducking_applied"] else "not applied"))
    if d["applied"]:
        print(r.heading("Applied"))
        print(r.bullets(d["applied"], limit=14))
    if d["sync_points"]:
        print(r.heading("Sync points"))
        for sp in d["sync_points"][:10]:
            print(f"  {sp['marker_time']:6.1f}s {sp['marker_type']:<17} ↔ {sp['music_event']:<12} {sp['offset_ms']:+7.1f} ms  {r.src_tag(sp['quality'])}")
    for w in d.get("warnings", []):
        print(r.yellow("  ! " + w))
    if d.get("command") and os.environ.get("MUSICCONTEXT_SHOW_COMMAND") or d.get("dry_run"):
        print(r.heading("Command"))
        print(r.wrap(d["command"], indent="  "))


def show_providers(d: dict) -> None:
    print(r.bold("Providers"))
    for p in d["providers"]:
        mark = r.green("available") if p["available"] else r.red("unavailable")
        print(f"\n  {r.bold(p['name']):<24} {mark}  {r.dim('/'.join(p['kinds']))}")
        if p.get("notes"):
            print(r.wrap(p["notes"], indent="     "))
        if p["requires_credentials"]:
            state = r.green("set") if p.get("credentials_present") else r.red("not set")
            print(f"     credentials: {p.get('credential_env') or '?'} ({state})")
        if p.get("sends_media_off_device"):
            print(r.yellow("     sends media off this machine"))
        if p.get("data_handling"):
            print(r.dim(r.wrap(p["data_handling"], indent="     ")))
    if d.get("local_only"):
        print(r.yellow("\n  local-only mode: providers that would send media off this machine are disabled."))
    for w in d.get("warnings", []):
        print(r.yellow("  ! " + w))


# ---------------------------------------------------------------- dispatch


def _out(args, payload: dict, human) -> None:
    fmt = args.format or ("json" if getattr(args, "json", False) else "human")
    if fmt == "json":
        print(json.dumps(payload, indent=None if args.quiet else 2, default=str))
    elif not args.quiet:
        human()
    else:
        pass


def _prefs(args) -> dict:
    keys = ("style", "mood", "genre", "instruments", "avoid", "bpm", "energy", "vocals", "era", "language", "audience",
            "platform", "content_type", "profile", "brief", "duration", "commercial_use", "require_verified_license", "marker")
    return {k: getattr(args, k) for k in keys if getattr(args, k, None) not in (None, [], False)}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    setup_logging(getattr(args, "verbose", False), getattr(args, "quiet", False))
    settings = load_settings(
        provider=getattr(args, "provider", None), model=getattr(args, "model", None),
        no_cache=getattr(args, "no_cache", False), verbose=getattr(args, "verbose", False),
        local_only=True if getattr(args, "local_only", False) else None,
    )
    ctx = OpContext.create(settings)
    try:
        return dispatch(args, ctx, parser)
    except MusicContextError as e:
        if getattr(args, "json", False) or getattr(args, "format", None) == "json":
            print(json.dumps({"error": e.to_dict()}, indent=2))
        else:
            print(r.red(f"error: {e.message}"), file=sys.stderr)
            if e.hint:
                print(r.dim(f"  -> {e.hint}"), file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0


def dispatch(args, ctx: OpContext, parser) -> int:  # noqa: PLR0911, PLR0912
    from ..agent import ops

    cmd = args.command
    if cmd == "inspect":
        d = ops.op_inspect(ctx, args.video)
        _out(args, d, lambda: show_inspect(d))
        return 0
    if cmd == "analyze":
        _rep, d = ops.op_analyze(ctx, args.video, transcript=args.transcript, cut_threshold=args.cut_threshold)
        _out(args, d, lambda: show_analysis(d, args.verbose))
        return 0
    if cmd == "plan":
        res, path, d = ops.op_plan(ctx, args.video, prefs=_prefs(args), variants=args.variants,
                                   transcript=args.transcript, output=args.output, save=not args.no_save, force=args.force)
        def human():
            m = res.analysis.metadata
            print(r.bold(f"{PROG} · {Path(m.path).name}"))
            print(_meta_line(m.model_dump(mode="json")))
            show_direction(d)
            for msg in res.messages:
                print(r.cyan("  i " + msg))
            if path:
                print(r.dim(f"\nArtifact: {path}"))
            print(r.dim(f"Next: {PROG} recommend {Path(m.path).name}"))
        _out(args, d, human)
        return 0
    if cmd == "recommend":
        res, path, d = ops.op_recommend(ctx, args.video, prefs=_prefs(args), limit=args.limit, provider=args.provider,
                                        transcript=args.transcript, artifact=args.artifact, output=args.output,
                                        save=not args.no_save, force=args.force)
        def human():
            print(r.bold(f"{PROG} · {Path(res.analysis.metadata.path).name}"))
            show_direction(d, show_why=False)
            show_candidates(d, args.limit)
            for msg in res.messages:
                print(r.cyan("  i " + msg))
            if path:
                print(r.dim(f"\nArtifact: {path}"))
        _out(args, d, human)
        return 0
    if cmd == "generate":
        res, path, d = ops.op_generate(ctx, args.video, prefs=_prefs(args), provider=args.provider, dest=args.dest,
                                       duration=args.duration, seed=args.seed, transcript=args.transcript,
                                       artifact=args.artifact, output=args.output, save=not args.no_save, force=args.force)
        def human():
            g = d["generation"]
            print(r.bold("Generated ") + str(g.get("artifact_path") or ""))
            for k in ("provider", "track_id", "duration", "requested_bpm", "measured_bpm", "license"):
                if k in g:
                    print(r.kv(k, str(g[k]), 18))
            for k, v in g.items():
                if k not in ("provider", "track_id", "duration", "requested_bpm", "measured_bpm", "license", "artifact_path"):
                    print(r.kv(k, str(v)[:90], 18))
            if path:
                print(r.dim(f"\nArtifact: {path}"))
        _out(args, d, human)
        return 0
    if cmd == "search":
        d = ops.op_search(ctx, text=args.text, video=args.video, prefs=_prefs(args), limit=args.limit,
                          provider=args.provider, artifact=args.artifact)
        def human():
            print(r.bold("Search") + r.dim(f"  {d['query']}"))
            for c_ in d["candidates"]:
                f = c_.get("features") or {}
                bpm = f.get("bpm")
                bpm_s = f"{bpm['value']:.0f} BPM" if bpm else "—"
                name = c_["title"] or c_["id"]
                print(f"  {name:<40} {r.dim(c_['provider']):<12} {bpm_s:>10}  {r.t(f.get('duration', 0)):>7}")
            if not d["candidates"]:
                print(r.dim("  no results"))
            for m in d["messages"]:
                print(r.cyan("  i " + m))
            for w in d["warnings"]:
                print(r.yellow("  ! " + w))
        _out(args, d, human)
        return 0
    if cmd == "sync":
        if not args.output:
            raise InvalidArgumentError("--output is required for sync.", "e.g. --output final.mp4 (the input video is never modified)")
        d = ops.op_sync(ctx, video=args.video, music=args.music, output=args.output, artifact=args.artifact,
                        prefs=_prefs(args), duck=args.duck, ducking_mode=args.ducking_mode, loop=args.loop,
                        target_lufs=args.target_lufs, keep_original_audio=args.keep_original_audio,
                        transcript=args.transcript, force=args.force, dry_run=args.dry_run, copy_video=args.copy_video)
        _out(args, d, lambda: show_sync(d))
        return 0
    if cmd == "render":
        return cmd_render(args, ctx)
    if cmd == "export":
        target = args.target
        d = ops.op_get_artifact(ctx, path=target if target.endswith(".musicctx") else None,
                                video=None if target.endswith(".musicctx") else target, full=args.full)
        _out(args, d, lambda: print(json.dumps(d, indent=2, default=str)))
        return 0
    if cmd == "library":
        return cmd_library(args, ctx, parser)
    if cmd == "providers":
        d = ops.op_providers(ctx)
        _out(args, d, lambda: show_providers(d))
        return 0
    if cmd == "config":
        return cmd_config(args, ctx, parser)
    if cmd == "profile":
        return cmd_profile(args, ctx, parser)
    if cmd == "doctor":
        from .doctor import run_doctor

        d = run_doctor(ctx)
        _out(args, d, lambda: show_doctor(d))
        return 0 if d["ok"] else 1
    if cmd == "init-agent":
        from ..agent.install import install

        d = install(ctx.settings, claude=args.claude, codex=args.codex, mcp=args.mcp, project=args.project,
                    force=args.force, dry_run=args.dry_run)
        _out(args, d, lambda: show_install(d))
        return 0 if d["ok"] else 1
    if cmd == "mcp":
        from ..agent.mcp_server import serve

        roots = [p for p in (args.roots or "").split(os.pathsep) if p] or None
        return serve(ctx.settings, roots=roots)
    parser.print_help()
    return 2


def cmd_render(args, ctx: OpContext) -> int:
    from ..agent import ops

    out = args.output or str(Path(args.video).with_name(Path(args.video).stem + "-scored.mp4"))
    if args.music:
        music = args.music
        extra = {}
    elif args.generate:
        res, _p, gd = ops.op_generate(ctx, args.video, prefs=_prefs(args), provider=args.provider, seed=args.seed, save=False)
        music = (res.generated.artifact_path if res.generated else None) or ""
        extra = {"generated": gd.get("generation", gd)}
        if not music:
            raise InvalidArgumentError("Generation produced no audio file.", "Run `musiccontext generate` on its own to see the provider error.")
    else:
        res, _p, rd = ops.op_recommend(ctx, args.video, prefs=_prefs(args), limit=1, provider=args.provider, save=False)
        if not res.selected or not res.selected.assessment.candidate.uri:
            msgs = "; ".join(res.messages) or "no candidates were available"
            raise InvalidArgumentError(f"No usable track to render: {msgs}", "Add music with `musiccontext library scan <dir>`, pass --music <file>, or use --generate.")
        music = res.selected.assessment.candidate.uri
        extra = {"selected": res.selected.assessment.candidate.model_dump(mode="json")}
    d = ops.op_sync(ctx, video=args.video, music=music, output=out, prefs=_prefs(args), duck=args.duck,
                    force=args.force, dry_run=args.dry_run)
    d.update(extra)
    _out(args, d, lambda: (print(r.dim(f"music: {music}")), show_sync(d)))
    return 0


def cmd_library(args, ctx: OpContext, parser) -> int:
    from ..agent import ops
    from ..music import library

    sc = getattr(args, "subcommand", None)
    if sc == "add":
        cand = library.add_track(ctx.settings, ctx.policy.input_file(args.path, "audio"), tags=args.tag, force=args.force)
        d = cand.model_dump(mode="json")
        def human():
            f = cand.features
            bpm = f"{f.bpm.value:.1f} BPM [{f.bpm.source}]" if f and f.bpm else "BPM unknown"
            print(r.green("added ") + f"{cand.title or cand.id}  {bpm}  {r.t(f.duration) if f else ''}")
            if cand.license:
                print(r.kv("license", f"{'verified' if cand.license.verified else 'unverified'} · commercial: {cand.license.commercial_use}"))
        _out(args, d, human)
        return 0
    if sc == "scan":
        res = library.scan_directory(ctx.settings, ctx.policy.input_dir(args.directory), recursive=args.recursive, force=args.force)
        d = {"added": len(res.added), "updated": len(res.updated), "unchanged": len(res.unchanged),
             "duplicates": len(res.duplicates), "errors": [{"path": p, "reason": why} for p, why in res.errors]}
        def human():
            print(r.green(f"added {len(res.added)}") + f", updated {len(res.updated)}, unchanged {len(res.unchanged)}, duplicates {len(res.duplicates)}")
            for p, why in res.errors[:10]:
                print(r.yellow(f"  ! {Path(p).name}: {why}"))
            print(r.dim(f"Next: {PROG} library search \"cinematic\""))
        _out(args, d, human)
        return 0
    if sc == "search":
        d = ops.op_library_search(ctx, args.text, limit=args.limit, bpm=args.bpm, vocals=args.vocals,
                                  min_duration=args.min_duration, commercial_use=args.commercial_use)
        def human():
            print(r.bold(f"{d['count']} track(s)") + r.dim(f"  {d['query']}"))
            for tr in d["tracks"]:
                f = tr.get("features") or {}
                bpm = f.get("bpm")
                lic = tr.get("license") or {}
                bpm_s = f"{bpm['value']:.0f}" if bpm else "—"
                title = (tr["title"] or "")[:36]
                verified = "verified" if lic.get("verified") else "unverified"
                print(f"  {tr['id'][:10]}  {title:<36} {bpm_s:>5} BPM  {r.t(f.get('duration', 0)):>7}  {r.dim(verified)}")
        _out(args, d, human)
        return 0
    if sc == "list":
        tracks = library.list_tracks(ctx.settings, limit=args.limit)
        d = {"count": len(tracks), "tracks": [t.model_dump(mode="json") for t in tracks]}
        def human():
            for t in tracks:
                print(f"  {t.id[:10]}  {(t.title or '')[:40]:<40} {r.dim(t.uri or '')}")
            if not tracks:
                print(r.dim("library is empty - run `musiccontext library scan <dir>`"))
        _out(args, d, human)
        return 0
    if sc == "remove":
        ok = library.remove_track(ctx.settings, args.track_id)
        d = {"removed": ok, "track_id": args.track_id}
        _out(args, d, lambda: print(r.green("removed") if ok else r.yellow("no such track (the audio file itself is never deleted)")))
        return 0
    if sc == "stats":
        d = library.library_stats(ctx.settings)
        def human():
            print(r.bold("Library"))
            for k, v in d.items():
                print(r.kv(k, json.dumps(v) if isinstance(v, (dict, list)) else str(v), 24))
        _out(args, d, human)
        return 0
    parser.parse_args([args.command, "--help"])
    return 2


def cmd_config(args, ctx: OpContext, parser) -> int:
    from ..engine.context import config_file

    sc = getattr(args, "subcommand", None) or "show"
    if sc == "show":
        d = ctx.settings.show()
        def human():
            print(r.bold("Configuration"))
            for k, v in d.items():
                print(r.kv(k, json.dumps(v) if isinstance(v, (dict, list)) else str(v), 22))
            print(r.dim("\nSecret values are never printed. Credentials are listed by name only."))
        _out(args, d, human)
        return 0
    if sc == "path":
        d = {"config_file": str(config_file()), "exists": config_file().is_file()}
        _out(args, d, lambda: print(d["config_file"]))
        return 0
    if sc == "set":
        allowed = {"provider": str, "model": str, "allow_remote": bool, "local_only": bool, "home": str,
                   "cache_dir": str, "max_output_chars": int, "max_download_bytes": int}
        if args.key not in allowed:
            raise InvalidArgumentError(f"Cannot set '{args.key}'.", f"Settable keys: {', '.join(sorted(allowed))}. "
                                       "Credentials are only ever read from environment variables, never written to the config file.")
        cf = config_file()
        cf.parent.mkdir(parents=True, exist_ok=True)
        lines = [ln for ln in (cf.read_text().splitlines() if cf.is_file() else []) if not ln.strip().startswith(f"{args.key} ")]
        typ = allowed[args.key]
        if typ is bool:
            val = str(args.value.strip().lower() in ("1", "true", "yes", "on")).lower()
        elif typ is int:
            try:
                val = str(int(args.value))
            except ValueError:
                raise InvalidArgumentError(f"'{args.key}' must be an integer.") from None
        else:
            val = json.dumps(args.value)
        lines.append(f"{args.key} = {val}")
        cf.write_text("\n".join(lines) + "\n")
        d = {"config_file": str(cf), "set": {args.key: args.value}}
        _out(args, d, lambda: print(r.green("set ") + f"{args.key} = {args.value}" + r.dim(f"\n  {cf}")))
        return 0
    parser.parse_args(["config", "--help"])
    return 2


def cmd_profile(args, ctx: OpContext, parser) -> int:
    from ..direction import profile as pm

    sc = getattr(args, "subcommand", None)
    if sc == "create":
        prof = pm.make(args.name, args.style, args.avoid, args.instruments, args.mood, args.vocals, args.notes)
        p = pm.save(ctx.settings, args.name, prof, force=args.force)
        d = {"profile": prof.model_dump(mode="json"), "path": str(p)}
        _out(args, d, lambda: print(r.green("created ") + str(p) + r.dim(f"\nUse it with: {PROG} plan video.mp4 --profile {args.name}")))
        return 0
    if sc == "list":
        names = pm.list_profiles(ctx.settings)
        d = {"profiles": names}
        _out(args, d, lambda: print("\n".join("  " + n for n in names) if names else r.dim("no profiles yet")))
        return 0
    if sc == "show":
        prof = pm.load(ctx.settings, args.name)
        d = prof.model_dump(mode="json")
        _out(args, d, lambda: print(json.dumps(d, indent=2)))
        return 0
    parser.parse_args(["profile", "--help"])
    return 2


def show_doctor(d: dict) -> None:
    print(r.bold(f"{PROG} doctor") + ("  " + (r.green("ok") if d["ok"] else r.yellow("issues found"))))
    for section, checks in d["sections"].items():
        print(r.heading(section))
        for ch in checks:
            icon = {"ok": r.green("ok  "), "warn": r.yellow("warn"), "fail": r.red("fail"), "info": r.dim("info")}[ch["status"]]
            print(f"  {icon} {ch['name']:<26} {ch['detail']}")
            if ch.get("hint"):
                print(r.dim(f"       -> {ch['hint']}"))
    if d.get("next"):
        print(r.heading("Next"))
        print(r.bullets(d["next"], limit=8))


def show_install(d: dict) -> None:
    print(r.bold("init-agent") + (r.dim("  (dry run — nothing was changed)") if d.get("dry_run") else ""))
    for act in d["actions"]:
        icon = {"created": r.green("created"), "updated": r.green("updated"), "skipped": r.dim("skipped"),
                "would-create": r.cyan("would create"), "would-update": r.cyan("would update"), "failed": r.red("failed")}.get(act["action"], act["action"])
        print(f"  {icon:<22} {act['target']}")
        if act.get("note"):
            print(r.dim(f"       {act['note']}"))
    for w in d.get("warnings", []):
        print(r.yellow("  ! " + w))
    if d.get("next"):
        print(r.heading("Next"))
        print(r.bullets(d["next"], limit=8))
