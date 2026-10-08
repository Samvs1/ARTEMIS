"""Milo's voice.

FishVoice sends text to Fish Audio and gets speech back. DemoVoice makes a soft
babble of marimba-like blips, one per word, so the whole audio path can be tried
without a key (and it fits Milo's chirpy sound).
"""
from __future__ import annotations

import array
import io
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
import wave

from mind.brain import describe_http_error, friendly_status, network_message
from mind.config import Settings

class VoiceError(Exception):
    """The voice could not be made. The message is written for a person to read."""


class _ModelRefused(Exception):
    """Fish Audio did not accept this model name. Try the next one."""


class _Busy(Exception):
    """Fish Audio is busy (429), had a server error, or could not be reached. Worth one retry."""


class FishVoice:
    """Text to speech through Fish Audio."""

    kind = "fish"
    # Fish Audio's own documents name these differently in different places, so we try each in turn
    # and remember the one that works. Set FISH_AUDIO_MODEL in .env to pin one.
    MODEL_NAMES = ("s2.1-pro", "s2-pro", "s1")

    def __init__(self, settings: Settings) -> None:
        self.key = settings.fish_key
        self.url = settings.fish_base + "/v1/tts"
        self.voice = settings.fish_voice
        self.latency = settings.fish_latency if settings.fish_latency in ("normal", "balanced") else "balanced"
        self.models = [settings.fish_model] if settings.fish_model else list(self.MODEL_NAMES)
        self.working: str | None = None
        self.retry_pause = 0.6

    @property
    def label(self) -> str:
        return "Fish Audio" + (f" ({self.working})" if self.working else "")

    def synthesize(self, text: str, fmt: str = "mp3") -> tuple[bytes, str]:
        order = ([self.working] if self.working else []) + [m for m in self.models if m != self.working]
        last = ""
        for model in order:
            try:
                audio, content_type = self._request_with_retry(model, text, fmt)
            except _ModelRefused as e:
                last = str(e)
                continue
            self.working = model
            return audio, content_type
        raise VoiceError(last or "Fish Audio did not accept any model name. Set FISH_AUDIO_MODEL in .env.")

    def _request_with_retry(self, model: str, text: str, fmt: str) -> tuple[bytes, str]:
        """One retry after a short pause when Fish Audio is busy or briefly unreachable.

        A sentence that fails would otherwise be skipped or spoken in another voice."""
        try:
            return self._request(model, text, fmt)
        except _Busy:
            time.sleep(self.retry_pause)
        try:
            return self._request(model, text, fmt)
        except _Busy as e:
            raise VoiceError(str(e)) from None

    def _request(self, model: str, text: str, fmt: str = "mp3") -> tuple[bytes, str]:
        body = {"text": text, "format": fmt, "latency": self.latency, "normalize": True}
        if self.voice:
            body["reference_id"] = self.voice
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json", "model": model},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                audio = resp.read()
                content_type = resp.headers.get_content_type() or ("audio/wav" if fmt == "wav" else "audio/mpeg")
        except urllib.error.HTTPError as e:
            detail = describe_http_error(e)
            about_voice = re.search(r"reference|voice|speaker", detail, re.I) is not None
            if e.code in (400, 404, 422) and not about_voice and len(self.models) > 1:
                raise _ModelRefused(f"Fish Audio did not accept model '{model}' (HTTP {e.code}: {detail})") from e
            if about_voice:
                raise VoiceError(f"Fish Audio does not accept the voice in FISH_AUDIO_VOICE_ID (HTTP {e.code}: {detail})") from e
            if e.code == 429 or e.code >= 500:
                raise _Busy(friendly_status("Fish Audio", e.code, detail)) from e
            raise VoiceError(friendly_status("Fish Audio", e.code, detail)) from e
        except (urllib.error.URLError, OSError) as e:
            raise _Busy(network_message("Fish Audio", "api.fish.audio", e)) from e
        if not content_type.startswith("audio/") or len(audio) < 200:
            raise VoiceError("Fish Audio sent back something that is not audio.")
        return audio, content_type


def babble_wav(text: str) -> bytes:
    """A soft marimba-like blip for each word, as a small WAV file."""
    rate = 22050
    scale = [523.25, 587.33, 659.25, 783.99, 880.0, 1046.5]
    samples = array.array("h")
    words = re.findall(r"[\w']+", text)[:60] or ["."]
    for word in words:
        freq = scale[sum(ord(c) for c in word) % len(scale)]
        count = int(rate * (0.10 + 0.025 * min(len(word), 6)))
        for i in range(count):
            t = i / rate
            envelope = min(1.0, t / 0.006) * math.exp(-t * 14)
            value = math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(2 * math.pi * freq * 4 * t)
            samples.append(int(max(-1.0, min(1.0, value * envelope * 0.55)) * 32767))
        samples.extend([0] * int(rate * 0.035))
    if sys.byteorder == "big":
        samples.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return out.getvalue()


class DemoVoice:
    """The babble voice. It is used when there is no Fish Audio key."""

    kind = "babble"
    label = "babble voice"

    def synthesize(self, text: str, fmt: str = "mp3") -> tuple[bytes, str]:
        return babble_wav(text), "audio/wav"          # always WAV, whatever format was asked for
