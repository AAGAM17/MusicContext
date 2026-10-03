"""The Python SDK: a thin, typed facade over the same operations the CLI and MCP server use."""

from __future__ import annotations

from pathlib import Path

from ..agent.ops import OpContext, build_plan, prefs_from_dict
from ..engine.artifacts import load_artifact
from ..engine.context import load_settings
from ..schemas import (
    AnalysisReport,
    MusicContextResult,
    MusicDirection,
    MusicGenerationResult,
    Preferences,
)


class MusicContext:
    """High-level entry point.

    >>> ctx = MusicContext()
    >>> analysis = ctx.analyze("demo.mp4")
    >>> plan = ctx.create_music_plan(analysis)
    >>> recs = ctx.recommend("demo.mp4")
    """

    def __init__(self, *, settings=None, restricted: bool = False, **overrides):
        self.settings = settings or load_settings(**overrides)
        self._ctx = OpContext.create(self.settings, restricted=restricted)

    # ---- analysis / planning
    def inspect(self, video: str | Path) -> dict:
        from ..agent import ops

        return ops.op_inspect(self._ctx, str(video))

    def analyze(self, video: str | Path, *, transcript: str | Path | None = None, cut_threshold: float = 0.28) -> AnalysisReport:
        from ..agent import ops

        rep, _ = ops.op_analyze(self._ctx, str(video), transcript=str(transcript) if transcript else None, cut_threshold=cut_threshold)
        return rep

    def create_music_plan(self, analysis: AnalysisReport, preferences: Preferences | dict | None = None, *, variants: int = 1) -> MusicContextResult:
        prefs = preferences if isinstance(preferences, Preferences) else prefs_from_dict(preferences)
        return build_plan(self._ctx, analysis, prefs, variants)

    def plan(self, video: str | Path, preferences: Preferences | dict | None = None, *, variants: int = 1,
             transcript: str | Path | None = None, save: bool = False) -> MusicContextResult:
        from ..agent import ops

        res, _path, _d = ops.op_plan(self._ctx, str(video), prefs=_as_dict(preferences), variants=variants,
                                     transcript=str(transcript) if transcript else None, save=save)
        return res

    # ---- music
    def search(self, text: str = "", *, video: str | Path | None = None, limit: int = 20, provider: str | None = None,
               preferences: Preferences | dict | None = None) -> dict:
        from ..agent import ops

        return ops.op_search(self._ctx, text=text, video=str(video) if video else None, prefs=_as_dict(preferences),
                             limit=limit, provider=provider)

    def recommend(self, video: str | Path, preferences: Preferences | dict | None = None, *, limit: int = 5,
                  provider: str | None = None, save: bool = False) -> MusicContextResult:
        from ..agent import ops

        res, _p, _d = ops.op_recommend(self._ctx, str(video), prefs=_as_dict(preferences), limit=limit, provider=provider, save=save)
        return res

    def generate(self, video: str | Path, preferences: Preferences | dict | None = None, *, provider: str | None = None,
                 dest: str | Path | None = None, duration: float | None = None, seed: int | None = None,
                 force: bool = False) -> MusicGenerationResult:
        from ..agent import ops

        res, _p, _d = ops.op_generate(self._ctx, str(video), prefs=_as_dict(preferences), provider=provider,
                                      dest=str(dest) if dest else None, duration=duration, seed=seed, save=False, force=force)
        if res.generated is None:
            from ..errors import ProviderError

            raise ProviderError("The generation provider returned no result.")
        return res.generated

    def sync(self, video: str | Path, music: str | Path, output: str | Path, *, duck: bool = True, loop: bool | None = None,
             target_lufs: float | None = None, keep_original_audio: bool = True, force: bool = False,
             dry_run: bool = False, artifact: str | Path | None = None) -> dict:
        from ..agent import ops

        return ops.op_sync(self._ctx, video=str(video), music=str(music), output=str(output), duck=duck, loop=loop,
                           target_lufs=target_lufs, keep_original_audio=keep_original_audio, force=force,
                           dry_run=dry_run, artifact=str(artifact) if artifact else None)

    # ---- misc
    def providers(self) -> list[dict]:
        from ..agent import ops

        return ops.op_providers(self._ctx)["providers"]

    def library_scan(self, directory: str | Path, *, recursive: bool = True, force: bool = False):
        from ..music.library import scan_directory

        return scan_directory(self.settings, self._ctx.policy.input_dir(directory), recursive=recursive, force=force)

    def load(self, artifact: str | Path) -> MusicContextResult:
        return load_artifact(Path(artifact))

    def direction(self, video: str | Path, **kw) -> MusicDirection:
        return self.plan(video, **kw).music_direction


def _as_dict(preferences) -> dict | None:
    if preferences is None:
        return None
    if isinstance(preferences, Preferences):
        d = preferences.model_dump(exclude_defaults=True)
        d.pop("user_markers", None)
        d["marker"] = [f"{t}={k}" for t, k in preferences.user_markers]
        return d
    return dict(preferences)
