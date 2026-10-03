"""Secrets must never reach a log, an error message or `config show`.

Every assertion here checks for the ABSENCE of the secret substring, so deleting the
redaction call makes the test fail rather than merely changing the formatting.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from musiccontext.engine.context import load_settings
from musiccontext.errors import ProviderError
from musiccontext.security.secrets import REDACTED, RedactingFilter, redact, redact_obj

# Fake credentials with the right shapes. None of these are real.
OPENAI = "sk-abcdefghijklmnopqrstuvwx"
AWS = "AKIA1234567890ABCDEF"
GITHUB = "ghp_abcdefghijklmnopqrstuvwxyz0123"
BEARER = "abc123def456ghi789jkl"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"request failed with key {OPENAI} on retry 2", OPENAI),
        (f"aws id {AWS} is misconfigured", AWS),
        (f"github token {GITHUB} rejected", GITHUB),
        (f"Authorization: Bearer {BEARER}", BEARER),
        ("api_key=secret123", "secret123"),
        ("password: hunter2", "hunter2"),
        ("provider_api_key=sk-livekey1234567890abcd", "sk-livekey1234567890abcd"),
    ],
)
def test_redact_removes_known_credential_shapes(text, secret):
    out = redact(text)
    assert secret not in out
    assert REDACTED in out


def test_redact_keeps_ordinary_text_intact():
    """A redactor that eats everything is useless; prove it is targeted."""
    assert redact("cinematic piano build at 92 bpm") == "cinematic piano build at 92 bpm"


def test_env_secret_is_redacted_by_value_anywhere(monkeypatch):
    """A credential's VALUE must be scrubbed even when it has no recognisable prefix."""
    value = "Z9bluehorizon42quay"
    monkeypatch.setenv("MUSICCONTEXT_FAKE_API_KEY", value)
    out = redact(f"provider replied 401 for {value} (see docs)")
    assert value not in out
    assert REDACTED in out


@pytest.mark.parametrize("name", ["ACME_KEY", "ACME_TOKEN", "ACME_SECRET", "ACME_PASSWORD"])
def test_every_secret_name_class_is_redacted_by_value(monkeypatch, name):
    value = f"value-for-{name.lower()}-0099"
    monkeypatch.setenv(name, value)
    assert value not in redact(f"stack trace mentions {value} here")


def test_redact_obj_recurses_and_redacts_secret_named_keys():
    obj = {
        "provider": "acme",
        "auth": {"api_key": "plainlookingvalue", "endpoint": "https://api.acme.test"},
        "history": [{"TOKEN": "abcdef1234567890"}, {"note": f"saw {OPENAI} in the log"}],
    }
    out = redact_obj(obj)
    assert out["auth"]["api_key"] == REDACTED
    assert out["history"][0]["TOKEN"] == REDACTED
    assert OPENAI not in json.dumps(out)
    # non-secret fields survive, otherwise the output would be useless
    assert out["auth"]["endpoint"] == "https://api.acme.test"
    assert out["provider"] == "acme"


def test_redacting_filter_scrubs_a_real_log_record():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(RedactingFilter())
    log = logging.getLogger("musiccontext.tests.redaction")
    log.handlers.clear()
    log.addHandler(handler)
    log.propagate = False
    log.setLevel(logging.INFO)
    try:
        log.warning("auth failed for %s", OPENAI)
        log.error("header was Bearer %s", BEARER)
    finally:
        log.handlers.clear()
    out = buf.getvalue()
    assert OPENAI not in out
    assert BEARER not in out
    assert out.count(REDACTED) == 2


def test_settings_show_lists_credential_names_but_never_values(settings, monkeypatch):
    """Users need to see WHICH credentials are configured without seeing them."""
    monkeypatch.setenv("MUSICCONTEXT_ACME_API_KEY", "supersecret")
    blob = json.dumps(load_settings().show())
    assert "supersecret" not in blob
    assert "MUSICCONTEXT_ACME_API_KEY" in blob
    assert REDACTED in blob
    assert settings.api_key_for("acme") == "supersecret"  # still usable internally


def test_typed_error_redacts_in_to_dict_and_str():
    err = ProviderError(f"call failed with key {OPENAI}", hint=f"unset MUSICCONTEXT_API_KEY={OPENAI}")
    assert OPENAI not in str(err)
    assert OPENAI not in json.dumps(err.to_dict())
    assert err.to_dict()["code"] == "provider_error"
