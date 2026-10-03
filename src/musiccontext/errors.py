"""Actionable errors. Every error carries a stable code and a hint on how to fix it."""

from __future__ import annotations


class MusicContextError(Exception):
    code = "error"
    exit_code = 1

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        from .security.secrets import redact

        d = {"code": self.code, "message": redact(self.message)}
        if self.hint:
            d["hint"] = redact(self.hint)
        return d

    def __str__(self) -> str:
        from .security.secrets import redact

        return redact(self.message + (f"\n  -> {self.hint}" if self.hint else ""))


class FFmpegMissingError(MusicContextError):
    code = "ffmpeg_missing"

    def __init__(self, tool: str = "ffmpeg"):
        super().__init__(
            f"{tool} was not found on PATH.",
            "Install FFmpeg (macOS: `brew install ffmpeg`; Debian/Ubuntu: `apt install ffmpeg`) "
            "or set MUSICCONTEXT_FFMPEG / MUSICCONTEXT_FFPROBE to the binaries.",
        )


class UnsupportedMediaError(MusicContextError):
    code = "unsupported_media"


class CorruptMediaError(MusicContextError):
    code = "corrupt_media"


class InvalidPathError(MusicContextError):
    code = "invalid_path"


class PermissionDeniedError(MusicContextError):
    code = "permission_denied"


class UnsafeURLError(MusicContextError):
    code = "unsafe_url"


class RemoteDisabledError(MusicContextError):
    code = "remote_disabled"

    def __init__(self):
        super().__init__(
            "Remote URLs are disabled.",
            "Set MUSICCONTEXT_ALLOW_REMOTE=1 (or `musiccontext config set allow_remote true`) to enable "
            "HTTPS downloads, or download the file yourself and pass a local path.",
        )


class ProviderError(MusicContextError):
    code = "provider_error"


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"


class NoProviderConfiguredError(ProviderError):
    code = "no_provider_configured"

    def __init__(self, kind: str = "music generation"):
        super().__init__(
            f"No {kind} provider is configured.",
            "Run `musiccontext providers` to see what is available. Built-in options: the local music "
            "library (`musiccontext library scan <dir>`) and the offline `procedural` generator. "
            "Third-party providers read MUSICCONTEXT_<PROVIDER>_API_KEY.",
        )


class NoMatchingMusicError(MusicContextError):
    code = "no_matching_music"


class LicenseUnverifiedError(MusicContextError):
    code = "license_unverified"


class InvalidArgumentError(MusicContextError):
    code = "invalid_argument"
    exit_code = 2


class ArtifactError(MusicContextError):
    code = "artifact_error"
