"""Settings for the mind server.

Values come from environment variables. If a file called .env exists in the
project folder, its lines (NAME=value) are read too, but a real environment
variable always wins over the file. Keys are never printed or logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def parse_env_text(text: str) -> dict[str, str]:
    """Read NAME=value lines. Blank lines and lines starting with # are ignored."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if name:
            values[name] = value
    return values


def load_env_file(path: Path = ENV_FILE) -> list[str]:
    """Copy values from the .env file into the environment.

    Real environment variables win, and empty values are skipped.
    Returns the names that were set (never the values).
    """
    if not path.is_file():
        return []
    names = []
    text = path.read_text(encoding="utf-8-sig", errors="replace")      # utf-8-sig: Notepad may add an invisible marker at the start
    for name, value in parse_env_text(text).items():
        if value and not os.environ.get(name):
            os.environ[name] = value
            names.append(name)
    return names


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    mock: bool = False                      # True means: ignore any keys and use the demo mind and voice
    deepseek_key: str = ""
    deepseek_base: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_thinking: str = "off"          # off | default. Thinking makes replies slow, so it is off for chat.
    fish_key: str = ""
    fish_base: str = "https://api.fish.audio"
    fish_voice: str = ""                    # optional: the ID of the voice to use (the reference_id)
    fish_model: str = ""                    # optional: leave empty to try the known model names in order
    fish_latency: str = "balanced"          # balanced = faster start, normal = a little better quality
    max_chats_per_hour: int = 120           # a safety net against a runaway loop spending your credit
    max_tts_chars_per_hour: int = 20000

    @property
    def use_deepseek(self) -> bool:
        return bool(self.deepseek_key) and not self.mock

    @property
    def use_fish(self) -> bool:
        return bool(self.fish_key) and not self.mock

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            host=e("MILO_HOST") or cls.host,
            port=_int("MILO_PORT", cls.port),
            deepseek_key=e("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base=(e("DEEPSEEK_BASE_URL") or cls.deepseek_base).rstrip("/"),
            deepseek_model=e("DEEPSEEK_MODEL") or cls.deepseek_model,
            deepseek_thinking=(e("DEEPSEEK_THINKING") or cls.deepseek_thinking).lower(),
            fish_key=e("FISH_AUDIO_API_KEY", "").strip(),
            fish_base=(e("FISH_AUDIO_BASE_URL") or cls.fish_base).rstrip("/"),
            fish_voice=e("FISH_AUDIO_VOICE_ID", "").strip(),
            fish_model=e("FISH_AUDIO_MODEL", "").strip(),
            fish_latency=e("FISH_AUDIO_LATENCY") or cls.fish_latency,
            max_chats_per_hour=_int("MILO_MAX_CHATS_PER_HOUR", cls.max_chats_per_hour),
            max_tts_chars_per_hour=_int("MILO_MAX_TTS_CHARS_PER_HOUR", cls.max_tts_chars_per_hour),
        )
