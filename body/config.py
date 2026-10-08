"""Settings for the body.

Values come from environment variables. The project's .env file is read too (the same
file the mind server uses), but a real environment variable always wins over the file.
Keys are never printed or logged. A number that cannot be read falls back to its
default, with a plain-word warning.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from mind.config import load_env_file, with_old_names


def log(kind: str, message: str) -> None:
    """One plain-word line, like the mind server's: `12:00:01 hears  hello there`."""
    print(f"{time.strftime('%H:%M:%S')} {kind:<6} {message}", flush=True)


@dataclass(frozen=True)
class BodySettings:
    mind_url: str = "http://127.0.0.1:8000"      # ARTEMIS_MIND_URL
    face_host: str = "127.0.0.1"                  # ARTEMIS_FACE_HOST
    face_port: int = 8001                         # ARTEMIS_FACE_PORT
    stt_order: tuple[str, ...] = ("openai", "local")   # ARTEMIS_STT_ORDER, comma separated
    openai_api_key: str = ""                      # OPENAI_API_KEY
    openai_stt_model: str = "gpt-4o-mini-transcribe"   # ARTEMIS_OPENAI_STT_MODEL
    local_stt_model: str = "tiny.en"              # ARTEMIS_LOCAL_STT_MODEL (faster-whisper size)
    wake_model: str = "hey_jarvis"                # ARTEMIS_WAKE_MODEL: openWakeWord name or .onnx path; "" = push to talk
    wake_threshold: float = 0.5                   # ARTEMIS_WAKE_THRESHOLD
    input_device: str = ""                        # ARTEMIS_INPUT_DEVICE: part of a device name, "" = default
    output_device: str = ""                       # ARTEMIS_OUTPUT_DEVICE
    barge_in: bool = False                        # ARTEMIS_BARGE_IN: talk over Arty to interrupt (needs echo cancelling)
    window_seconds: float = 6.0                   # ARTEMIS_WINDOW_SECONDS: keep listening after Arty speaks
    thinking_chirp: bool = True                   # ARTEMIS_THINKING_CHIRP: a chirp when Arty has heard you and starts thinking


_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")
_NO_WAKE_WORD = ("none", "off", "push", "push-to-talk")      # spellings that mean "use the keyboard instead"


def _text(env: dict, name: str, default: str) -> str:
    return (env.get(name) or "").strip() or default


def _number(env: dict, name: str, default, kind, low, high):
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = kind(raw)
        if not low <= value <= high:                  # also catches nan, which compares false with everything
            raise ValueError
    except ValueError:
        log("config", f"{name} should be a number from {low} to {high}, but it is {raw!r}. Using {default}.")
        return default
    return value


def _flag(env: dict, name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    log("config", f"{name} should be yes or no, but it is {raw!r}. Using {'yes' if default else 'no'}.")
    return default


def load_settings(env: dict | None = None) -> BodySettings:
    """Read the settings.

    With no argument it uses the real environment plus the project's .env file.
    Pass a dict to use exactly those values (the .env file is then not read), which is what tests do.
    """
    if env is None:
        load_env_file()
        env = dict(os.environ)
    else:
        env = with_old_names(dict(env))
    d = BodySettings()
    order = tuple(dict.fromkeys(n.strip().lower() for n in (env.get("ARTEMIS_STT_ORDER") or "").split(",") if n.strip()))
    wake = d.wake_model
    if "ARTEMIS_WAKE_MODEL" in env and env["ARTEMIS_WAKE_MODEL"] is not None:      # set but empty means push to talk
        wake = env["ARTEMIS_WAKE_MODEL"].strip()
        if wake.lower() in _NO_WAKE_WORD:
            wake = ""
    return BodySettings(
        mind_url=_text(env, "ARTEMIS_MIND_URL", d.mind_url).rstrip("/"),
        face_host=_text(env, "ARTEMIS_FACE_HOST", d.face_host),
        face_port=_number(env, "ARTEMIS_FACE_PORT", d.face_port, int, 0, 65535),
        stt_order=order or d.stt_order,
        openai_api_key=(env.get("OPENAI_API_KEY") or "").strip(),
        openai_stt_model=_text(env, "ARTEMIS_OPENAI_STT_MODEL", d.openai_stt_model),
        local_stt_model=_text(env, "ARTEMIS_LOCAL_STT_MODEL", d.local_stt_model),
        wake_model=wake,
        wake_threshold=_number(env, "ARTEMIS_WAKE_THRESHOLD", d.wake_threshold, float, 0.0, 1.0),
        input_device=_text(env, "ARTEMIS_INPUT_DEVICE", d.input_device),
        output_device=_text(env, "ARTEMIS_OUTPUT_DEVICE", d.output_device),
        barge_in=_flag(env, "ARTEMIS_BARGE_IN", d.barge_in),
        window_seconds=_number(env, "ARTEMIS_WINDOW_SECONDS", d.window_seconds, float, 0.0, 600.0),
        thinking_chirp=_flag(env, "ARTEMIS_THINKING_CHIRP", d.thinking_chirp),
    )
