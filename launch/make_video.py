"""Render the MusicContext launch film's picture: 1920x1080, 30 fps, 32 s, silent.

Eight 4-second scenes on a regular cut grid. The score is NOT made here: the finished silent film is
analysed, planned, generated and mixed by MusicContext itself (see launch/score.sh).

    python launch/make_video.py launch/film_silent.mp4
"""

from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS, DUR = 1920, 1080, 30, 32.0
SCENE = 4.0
INK, CREAM, CORAL = (21, 19, 15), (243, 238, 228), (217, 119, 87)
MUTED_D, MUTED_L = (150, 144, 130), (112, 106, 94)
GREEN, GOLD, SKY = (127, 191, 154), (229, 192, 123), (140, 178, 222)
TAG = {"user": CORAL, "detected": GREEN, "inferred": SKY, "estimated": GOLD}
DATA = json.loads((Path(__file__).parent / "demo_data.json").read_text())
F = "/System/Library/Fonts/"


@lru_cache(maxsize=None)
def font(kind: str, size: int, weight: str = "Regular"):
    path = {"serif": "NewYork.ttf", "sans": "SFNS.ttf", "mono": "SFNSMono.ttf"}[kind]
    f = ImageFont.truetype(F + path, size)
    try:
        f.set_variation_by_name(weight)
    except Exception:  # noqa: BLE001 - a missing weight name falls back to the default instance
        pass
    return f


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def prog(t, t0, dur):
    return clamp((t - t0) / dur)


def out_cubic(x):
    return 1 - (1 - x) ** 3


def out_expo(x):
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def in_out(x):
    return x * x * (3 - 2 * x)


def rgba(c, a=1.0):
    return (*c, int(255 * clamp(a)))


_CANVAS = None  # the frame being drawn; set in frame()


def text(d, xy, s, f, color, a=1.0, anchor="ls"):
    """Pillow ignores the alpha of a text fill, so draw the glyphs into an L mask and paste the colour through it."""
    a = clamp(a)
    if a <= 0.004:
        return
    x0, y0, x1, y1 = (int(v) for v in d.textbbox(xy, s, font=f, anchor=anchor))
    x0, y0, x1, y1 = x0 - 6, y0 - 6, x1 + 7, y1 + 7
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(mask).text((xy[0] - x0, xy[1] - y0), s, font=f, fill=int(255 * a), anchor=anchor)
    _CANVAS.paste(color, (x0, y0, x1, y1), mask)


def words(d, line, cx, y, f, color, lt, t0, step=0.26, dur=0.55, rise=26, colors=None):
    sp = f.getlength(" ")
    ws = [f.getlength(w) for w in line]
    x = cx - (sum(ws) + sp * (len(line) - 1)) / 2
    for i, w in enumerate(line):
        a = out_cubic(prog(lt, t0 + i * step, dur))
        text(d, (x, y + (1 - a) * rise), w, f, (colors or {}).get(i, color), a)
        x += ws[i] + sp


def aa_layer(box, draw_fn):
    """Draw at 2x into a small layer and downsample: anti-aliased lines without a 4K frame."""
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", ((x1 - x0) * 2, (y1 - y0) * 2), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer, "RGBA"), lambda x, y: ((x - x0) * 2, (y - y0) * 2))
    return layer.resize((x1 - x0, y1 - y0), Image.LANCZOS), (x0, y0)


# ---------------------------------------------------------------------------------------- scenes
def s1(c, d, lt):  # dark: the premise
    f = font("serif", 104, "Medium")
    words(d, ["AI", "agents", "can", "generate"], W / 2, 500, f, CREAM, lt, 0.35)
    words(d, ["remarkable", "video."], W / 2, 640, f, CREAM, lt, 1.55, colors={1: CORAL})


def s2(c, d, lt):  # cream: the problem
    f = font("serif", 104, "Medium")
    words(d, ["They", "still", "choose", "the", "music"], W / 2, 470, f, INK, lt, 0.25, step=0.22)
    words(d, ["by", "vibe."], W / 2, 610, f, INK, lt, 1.5, step=0.3)
    sw = f.getlength("by vibe.")
    p = out_expo(prog(lt, 2.5, 0.45))
    d.line([(W / 2 - sw / 2 - 12, 585), (W / 2 - sw / 2 - 12 + (sw + 24) * p, 585)], fill=rgba(CORAL), width=9)
    a = out_cubic(prog(lt, 3.0, 0.6))
    text(d, (W / 2, 800), "tempo: unknown    cuts: ignored    reveal: missed", font("mono", 30), MUTED_L, a, "mm")


def s3(c, d, lt):  # dark: the cost, on a timeline
    text(d, (W / 2, 220), "Music that ignores the edit.", font("serif", 84, "Medium"), CREAM, out_cubic(prog(lt, 0.2, 0.7)), "mm")
    x0, x1, yc, ym = 300, 1660, 520, 700
    text(d, (130, yc + 10), "the edit", font("sans", 30, "Semibold"), MUTED_D, out_cubic(prog(lt, 0.4, 0.5)))
    text(d, (130, ym + 10), "the music", font("sans", 30, "Semibold"), MUTED_D, out_cubic(prog(lt, 0.4, 0.5)))
    head = x0 + (x1 - x0) * in_out(prog(lt, 0.5, 3.0))
    shifts = [34, -41, 58, -27, 49, -63, 38]
    layer, org = aa_layer((100, 440, 1820, 800), lambda dr, m: _s3_draw(dr, m, x0, x1, yc, ym, head, shifts, lt))
    c.paste(layer.convert("RGB"), org, layer.getchannel("A"))
    ax = x0 + (x1 - x0) * 0.62
    p = out_expo(prog(lt, 2.7, 0.35))
    if p > 0:
        d.line([(ax, 430), (ax, 760)], fill=rgba(CREAM, 0.5 * p), width=3)
        text(d, (ax - 14, 420), "reveal", font("sans", 28, "Semibold"), CREAM, p, "rs")
        text(d, (ax + 40, ym - 76 - (1 - p) * 14), "+900 ms late", font("serif", 52, "Medium"), CORAL, p, "lm")
    text(d, (W / 2, 900), "illustration of a typical mismatch", font("mono", 26), MUTED_D, out_cubic(prog(lt, 2.9, 0.5)), "mm")


def _s3_draw(dr, m, x0, x1, yc, ym, head, shifts, lt):
    n = 7
    dr.line([m(x0, yc), m(x1, yc)], fill=rgba(CREAM, 0.18), width=4)
    dr.line([m(x0, ym), m(x1, ym)], fill=rgba(CREAM, 0.18), width=4)
    for k in range(n):
        x = x0 + (x1 - x0) * k / (n - 1)
        if x <= head:
            dr.line([m(x, yc - 34), m(x, yc + 34)], fill=rgba(CREAM), width=6)
        if x + shifts[k] <= head:
            dr.line([m(x + shifts[k], ym - 34), m(x + shifts[k], ym + 34)], fill=rgba(CORAL), width=6)
    dr.line([m(head, 450), m(head, 790)], fill=rgba(CREAM, 0.35), width=3)


def s4(c, d, lt):  # coral: the reveal
    for i in range(3):  # shockwaves start outside the wordmark and leave the frame, so they never cross the text
        p = prog(lt, 0.0 + i * 0.28, 1.8)
        r = 780 + 900 * out_cubic(p)
        d.ellipse([W / 2 - r, 500 - r * 0.62, W / 2 + r, 500 + r * 0.62], outline=rgba(INK, 0.3 * (1 - p)), width=3)
    e = out_expo(prog(lt, 0.0, 0.9))
    text(d, (W / 2, 500), "MusicContext", font("serif", int(214 * (0.9 + 0.1 * e)), "Semibold"), INK, clamp(e * 1.4), "mm")
    text(d, (W / 2, 690), "Music direction for AI agents.", font("sans", 56, "Medium"), CREAM, out_cubic(prog(lt, 0.9, 0.7)), "mm")
    flash = 0.4 * (1 - prog(lt, 0.0, 0.35))
    if flash > 0:
        d.rectangle([0, 0, W, H], fill=rgba((255, 255, 255), flash))


def s5(c, d, lt):  # dark: the pipeline
    text(d, (W / 2, 210), "From the picture to the mix.", font("serif", 80, "Medium"), CREAM, out_cubic(prog(lt, 0.1, 0.6)), "mm")
    names = ["video", "story", "direction", "soundtrack", "sync"]
    caps = ["cuts: detected", "roles: inferred", "a plan + a brief", "measured, not assumed", "ducked and aligned"]
    pw, ph, gap = 290, 120, 50
    x = (W - (5 * pw + 4 * gap)) / 2
    for i, nm in enumerate(names):
        a = out_cubic(prog(lt, 0.45 + 0.5 * i, 0.5))
        top = 470
        d.rounded_rectangle([x, top, x + pw, top + ph], radius=60, outline=rgba(CREAM, 0.35), width=3)
        if a > 0:
            d.rounded_rectangle([x, top, x + pw, top + ph], radius=60, fill=rgba(CORAL, a))
        text(d, (x + pw / 2, top + ph / 2), nm, font("sans", 40, "Semibold"), INK if a > 0.5 else CREAM, 1, "mm")
        text(d, (x + pw / 2, top + ph + 60 - (1 - a) * 10), caps[i], font("mono", 25), MUTED_D, a, "mm")
        if i < 4:
            ax = x + pw + 8
            d.line([(ax, top + ph / 2), (ax + gap - 16, top + ph / 2)], fill=rgba(CREAM, 0.45 * out_cubic(prog(lt, 0.7 + 0.5 * i, 0.4))), width=3)
        x += pw + gap


def s6(c, d, lt):  # cream: provenance
    f = font("serif", 84, "Medium")
    text(d, (160, 250), "Every number says", f, INK, out_cubic(prog(lt, 0.1, 0.6)))
    text(d, (160, 350), "how it was obtained.", f, INK, out_cubic(prog(lt, 0.35, 0.6)))
    d.rounded_rectangle([160, 450, 1760, 880], radius=30, fill=rgba(INK))
    rows = [("11.0s", "reveal", "peak", "user"), ("17.0s", "hard_cut", "none", "detected"),
            ("17.0s", "cta", "resolve", "inferred"), ("2.0s", "voiceover_start", "duck", "estimated")]
    mf = font("mono", 38)
    for i, (t, kind, act, tag) in enumerate(rows):
        t0 = 0.7 + i * 0.55
        n = prog(lt, t0, 0.45)
        y = 545 + i * 85
        full = f"{t:<7}{kind:<17}{act:<9}"
        shown = full[: int(len(full) * n)]
        text(d, (215, y), shown, mf, CREAM, 1)
        if n >= 1:
            text(d, (215 + mf.getlength(full), y), f"[{tag}]", mf, TAG[tag], out_cubic(prog(lt, t0 + 0.45, 0.25)))
    text(d, (160, 950), "told by you  ·  measured  ·  inferred  ·  estimated   —   and it survives into the JSON.",
         font("sans", 32), MUTED_L, out_cubic(prog(lt, 3.0, 0.6)))


def s7(c, d, lt):  # dark: plan vs measured
    text(d, (W / 2, 200), "Planned. Then measured.", font("serif", 84, "Medium"), CREAM, out_cubic(prog(lt, 0.1, 0.6)), "mm")
    cx0, cx1, base, top = 260, 1660, 800, 330
    X = lambda t: cx0 + (cx1 - cx0) * t / 24  # noqa: E731
    Y = lambda e: base - (base - top) * e  # noqa: E731
    tmax = 24 * in_out(prog(lt, 0.3, 2.7))
    arc = [(t, e) for t, e in DATA["arc"] if t <= tmax]
    if tmax < 24 and arc:
        t_, e_ = arc[-1]
        nxt = next(((tt, ee) for tt, ee in DATA["arc"] if tt > tmax), None)
        if nxt and nxt[0] != t_:
            arc.append((tmax, e_ + (nxt[1] - e_) * (tmax - t_) / (nxt[0] - t_)))
    meas = [(t, e) for t, e in DATA["measured"] if t <= tmax]

    def draw(dr, m):
        for g in (0, 0.5, 1.0):
            dr.line([m(cx0, Y(g)), m(cx1, Y(g))], fill=rgba(CREAM, 0.14 if g else 0.3), width=2)
        if len(arc) > 1:
            dr.line([m(X(t), Y(e)) for t, e in arc], fill=rgba(CORAL), width=14, joint="curve")
        if len(meas) > 1:
            dr.line([m(X(t), Y(e)) for t, e in meas], fill=rgba(GREEN), width=10, joint="curve")
        for t, e in meas:
            x, y = m(X(t), Y(e))
            dr.ellipse([x - 9, y - 9, x + 9, y + 9], fill=rgba(GREEN))

    layer, org = aa_layer((200, 280, 1720, 840), draw)
    c.paste(layer.convert("RGB"), org, layer.getchannel("A"))
    lf = font("sans", 30, "Semibold")
    a = out_cubic(prog(lt, 0.5, 0.5))
    d.line([(260, 868), (310, 868)], fill=rgba(CORAL, a), width=8)
    text(d, (326, 879), "planned arc", lf, CREAM, a)
    d.line([(560, 868), (610, 868)], fill=rgba(GREEN, a), width=8)
    text(d, (626, 879), "generated track, measured", lf, CREAM, a)
    if tmax >= 11:
        p = out_expo(prog(tmax, 11, 1.2))
        d.line([(X(11), 300), (X(11), base)], fill=rgba(CREAM, 0.7 * p), width=3)
    b = out_cubic(prog(lt, 3.0, 0.5))
    text(d, (W / 2, 965), "reveal 11.0 s   →   nearest measured downbeat 11.003 s   (+3 ms)", font("sans", 38, "Semibold"), CREAM, b, "mm")


def s8(c, d, lt):  # cream: the close
    f = font("serif", 116, "Semibold")
    text(d, (W / 2, 420), "Music direction", f, INK, out_cubic(prog(lt, 0.2, 0.8)), "mm")
    text(d, (W / 2, 560), "for AI agents.", f, INK, out_cubic(prog(lt, 0.55, 0.8)), "mm")
    uf = font("mono", 46)
    a = out_cubic(prog(lt, 1.4, 0.6))
    url = "github.com/AAGAM17/MusicContext"
    text(d, (W / 2, 730), url, uf, INK, a, "mm")
    w = uf.getlength(url) * out_expo(prog(lt, 1.7, 0.7))
    d.line([(W / 2 - uf.getlength(url) / 2, 770), (W / 2 - uf.getlength(url) / 2 + w, 770)], fill=rgba(CORAL), width=6)
    text(d, (W / 2, 860), "Local-first   ·   CLI   ·   MCP   ·   Claude Code Skill   ·   Apache-2.0",
         font("sans", 32), MUTED_L, out_cubic(prog(lt, 2.2, 0.6)), "mm")


SCENES = [(s1, "dark"), (s2, "light"), (s3, "dark"), (s4, "coral"), (s5, "dark"), (s6, "light"), (s7, "dark"), (s8, "light")]
_GRAIN = None
_VIG = None


def _post(img, tone, i):
    global _GRAIN, _VIG
    if _GRAIN is None:
        rng = np.random.default_rng(3)
        _GRAIN = [rng.normal(0, 1, (H, W, 1)).astype(np.float32) for _ in range(6)]
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        _VIG = (1 - 0.22 * np.clip(r - 0.35, 0, 1) ** 1.6)[..., None]
    a = np.asarray(img, dtype=np.float32) * _VIG + _GRAIN[i % 6] * (3.6 if tone == "dark" else 2.4)
    return np.clip(a, 0, 255).astype(np.uint8)


def frame(i):
    t = i / FPS
    s = min(int(t // SCENE), len(SCENES) - 1)
    lt = t - s * SCENE
    fn, tone = SCENES[s]
    bg = {"dark": INK, "light": CREAM, "coral": CORAL}[tone]
    global _CANVAS
    c = _CANVAS = Image.new("RGB", (W, H), bg)  # RGB on purpose: ImageDraw only alpha-blends onto RGB, so fades are real fades
    d = ImageDraw.Draw(c, "RGBA")
    fn(c, d, lt)
    img = c
    z = 1.0 + 0.03 * (lt / SCENE if s % 2 == 0 else 1 - lt / SCENE)  # slow push in / pull out
    img = img.transform((W, H), Image.AFFINE, (1 / z, 0, W / 2 * (1 - 1 / z), 0, 1 / z, H / 2 * (1 - 1 / z)), Image.BICUBIC)
    arr = _post(img, tone, i)
    if s == len(SCENES) - 1 and lt > 3.35:  # fade to ink at the very end
        arr = (arr * (1 - prog(lt, 3.35, 0.65)) + np.array(INK) * prog(lt, 3.35, 0.65)).astype(np.uint8)
    return arr.tobytes()


def main(out):
    n = int(DUR * FPS)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)  # noqa: S603
    with Pool(7) as pool:
        for k, buf in enumerate(pool.imap(frame, range(n), chunksize=4)):
            p.stdin.write(buf)
            if k % 60 == 0:
                print(f"{k}/{n}", flush=True)
    p.stdin.close()
    p.wait()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--still":  # --still SECONDS out.png  (for checking a frame)
        Image.frombuffer("RGB", (W, H), frame(int(float(sys.argv[2]) * FPS)), "raw", "RGB", 0, 1).save(sys.argv[3])
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else "launch/film_silent.mp4")
