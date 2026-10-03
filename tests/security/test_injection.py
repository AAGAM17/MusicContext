"""Media-derived text is data, never an instruction.

OCR lines, subtitle cues, transcripts and container tags all come from a file someone
else produced. These tests feed real injection payloads through the sanitizers and
assert the payload cannot survive into output as live text or as a leaked credential.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from musiccontext.analysis.speech import parse_subtitles
from musiccontext.analysis.video import inspect_video
from musiccontext.security.content import (
    MAX_FIELD,
    bound_json,
    injection_warnings,
    looks_like_instruction,
    sanitize_meta,
    sanitize_tags,
    sanitize_text,
)
from musiccontext.security.secrets import REDACTED

PAYLOADS = json.loads((Path(__file__).parent / "fixtures" / "malicious_metadata.json").read_text())
INJECTIONS: dict[str, str] = PAYLOADS["injections"]
SECRET: str = PAYLOADS["secret"]
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x1b]")


@pytest.mark.parametrize("field", sorted(INJECTIONS))
def test_sanitize_text_strips_control_chars_collapses_space_and_bounds_length(field):
    out = sanitize_text(INJECTIONS[field])
    assert not CONTROL.search(out), f"control/ANSI bytes survived in {field}"
    assert "\n" not in out and "  " not in out
    assert len(out) <= MAX_FIELD


def test_sanitize_text_redacts_a_key_hidden_in_media_metadata():
    out = sanitize_text(INJECTIONS["metadata_value"])
    assert SECRET not in out
    assert REDACTED in out


def test_sanitize_text_bounds_a_10k_payload():
    raw = PAYLOADS["long_string"]
    assert len(raw) > 10_000  # the fixture really is oversized
    out = sanitize_text(raw)
    assert len(out) <= MAX_FIELD
    assert out.endswith("…")


@pytest.mark.parametrize("field", sorted(INJECTIONS))
def test_looks_like_instruction_flags_every_payload(field):
    assert looks_like_instruction(INJECTIONS[field]) is True


@pytest.mark.parametrize("text", PAYLOADS["benign"])
def test_looks_like_instruction_ignores_ordinary_music_language(text):
    assert looks_like_instruction(text) is False


def test_injection_warnings_name_the_field_and_are_safe_to_print():
    fields = {k: sanitize_text(v) for k, v in INJECTIONS.items()}
    warnings = injection_warnings(fields)
    assert len(warnings) == len(fields)
    joined = " ".join(warnings)
    for name in fields:
        assert f"'{name}'" in joined
    # the warning must not re-emit the payload: no control bytes, no secret, no raw command
    assert not CONTROL.search(joined)
    assert SECRET not in joined
    assert "rm -rf" not in joined


def test_injection_warnings_stay_quiet_for_clean_fields():
    assert injection_warnings({"title": "cinematic piano build"}) == []


def test_subtitle_text_never_survives_into_speech_segments():
    """parse_subtitles keeps timings and word COUNTS only; cue text is dropped on the floor."""
    # a blank line terminates an SRT cue, so fold embedded newlines into the single cue line
    cues = [v.replace("\n", " ") for v in INJECTIONS.values()]
    srt = "\n".join(
        f"{i + 1}\n00:00:{2 * i:02d},000 --> 00:00:{2 * i + 1:02d},500\n{c}\n"
        for i, c in enumerate(cues)
    )
    segs = parse_subtitles(srt, provenance="test fixture subtitles")
    assert len(segs) == len(cues)
    assert all(s.words and s.words > 0 for s in segs)
    blob = json.dumps([s.model_dump(mode="json") for s in segs])
    for needle in ("Ignore", "rm -rf", "attacker.example", SECRET, "SYSTEM:", "\u001b"):
        assert needle not in blob, f"{needle!r} leaked out of parse_subtitles"


def test_sanitize_meta_drops_nested_structures_and_bounds_item_count():
    tags = {"nested": {"a": 1}, "listy": [1, 2, 3], "title": INJECTIONS["title_ansi"]}
    tags.update({f"pad{i}": str(i) for i in range(60)})
    out = sanitize_meta(tags)
    assert "nested" not in out and "listy" not in out
    assert len(out) <= 32
    assert all(isinstance(v, str) for v in out.values())


def test_sanitize_meta_redacts_a_key_carried_in_a_container_tag():
    out = sanitize_meta({"comment": INJECTIONS["metadata_value"]})
    assert SECRET not in out["comment"]
    assert REDACTED in out["comment"]


def test_sanitize_tags_lowercases_bounds_and_dedupes():
    tags = sanitize_tags([INJECTIONS["tag_newline"], "Piano", "piano", *[f"t{i}" for i in range(50)]])
    assert len(tags) <= 32
    assert tags.count("piano") == 1
    assert all(not CONTROL.search(t) and t == t.lower() for t in tags)


def test_bound_json_caps_a_long_list_and_marks_the_truncation():
    out = bound_json(list(range(500)), max_list=200)
    assert len(out) == 201
    assert "_truncated" in out[-1]
    json.loads(json.dumps(out))  # still valid JSON


def test_bound_json_caps_an_oversized_object_and_marks_it():
    out = bound_json({"blob": "x" * 50_000}, max_chars=2_000)
    assert "_truncated_output" in out
    json.loads(json.dumps(out))


def test_bound_json_leaves_small_payloads_untouched():
    payload = {"bpm": 120, "tags": ["piano", "warm"]}
    assert bound_json(payload) == payload


@pytest.mark.ffmpeg
def test_container_metadata_injection_is_sanitized_end_to_end(demo_video, settings, tmp_path):
    """The real ffprobe path: a tainted title must come back clean from inspect_video."""
    title = INJECTIONS["title_ansi"].replace("\x00", "")  # argv cannot carry a NUL byte
    tainted = tmp_path / "tainted.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-nostdin", "-i", str(demo_video), "-c", "copy",
         "-metadata", f"title={title}", "-metadata", f"comment={INJECTIONS['metadata_value']}",
         str(tainted)],
        check=True,
    )
    tags = inspect_video(tainted, settings, with_hash=False).container_tags
    assert tags, "ffmpeg did not write the metadata; the test would be vacuous"
    blob = json.dumps(tags)
    assert not CONTROL.search(blob)
    assert SECRET not in blob
    assert REDACTED in tags["comment"]
    assert all(len(v) <= MAX_FIELD for v in tags.values())
    # ...and the pipeline flags it as instruction-shaped rather than silently trusting it
    assert injection_warnings(tags)
