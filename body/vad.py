"""Voice activity: is someone talking, and where does one sentence start and end?

`VoiceDetector` says yes or no for one 30 ms frame. It uses `webrtcvad` if that is installed,
otherwise a simple loudness detector that learns how noisy the room is.

`Utterances` collects frames into whole sentences. It keeps a little sound from before the
voice started (so the first word is not clipped) and hands back the finished sentence as PCM.
"""
from __future__ import annotations

import io
import wave
from collections import deque

import numpy as np

from body.audio import FRAME, RATE, log

FRAME_MS = FRAME * 1000 // RATE      # 30


class VoiceDetector:
    """Says whether a frame (30 ms, 16 kHz mono int16 bytes) holds speech."""

    MIN_RMS = 120.0          # never call it speech below this loudness (int16 units), however quiet the room
    STUCK_FRAMES = 333       # about 10 seconds
    RATIO = 3.0              # speech must be this many times louder than the room's noise floor

    def __init__(self, aggressiveness: int = 2, use_webrtc: bool = True) -> None:
        self._webrtc = None
        self.floor: float | None = None            # the room's noise level, learned as we go
        self._run = 0                              # how many "speech" frames in a row
        if use_webrtc:
            try:
                import webrtcvad                   # noqa: PLC0415
                self._webrtc = webrtcvad.Vad(aggressiveness)
            except Exception as exc:
                log("vad", f"webrtcvad is not available ({exc}); using the simple loudness detector instead")
        if self._webrtc is not None:
            log("vad", f"using webrtcvad (mode {aggressiveness})")

    @property
    def uses_webrtc(self) -> bool:
        return self._webrtc is not None

    def reset(self) -> None:
        """Forget what we learned about the room's noise."""
        self.floor = None
        self._run = 0

    def is_speech(self, frame: bytes) -> bool:
        if self._webrtc is not None and len(frame) == FRAME * 2:
            try:
                return bool(self._webrtc.is_speech(frame, RATE))
            except Exception:
                pass
        return self._energy(frame)

    def _energy(self, frame: bytes) -> bool:
        x = np.frombuffer(frame[: len(frame) // 2 * 2], dtype=np.int16).astype(np.float32)
        if len(x) == 0:
            return False
        rms = float(np.sqrt(np.mean(x * x)))
        if self.floor is None:
            # First guess: the first frame, but not above 150, so speech that starts right away still counts.
            self.floor = min(max(rms, 20.0), 150.0)
        speech = rms > max(self.floor * self.RATIO, self.MIN_RMS)
        self._run = self._run + 1 if speech else 0
        # Follow the room noise: quickly when it gets quieter, slowly when it gets louder.
        # While someone is talking it barely moves, so a long sentence does not raise the floor.
        # But if "speech" never stops for 10 seconds it is really a noisy room, so learn it.
        if rms < self.floor:
            self.floor += (rms - self.floor) * 0.1
        else:
            slow = speech and self._run < self.STUCK_FRAMES
            self.floor += (rms - self.floor) * (0.0005 if slow else 0.05)
        self.floor = max(self.floor, 5.0)
        return speech


class Utterances:
    """Turns a stream of frames into finished utterances.

    Time is counted in frames fed (30 ms each), not by the clock, so it works the same with the
    real microphone and with a fake one that runs faster than real time.
    """

    TRAIL_FRAMES = 6         # keep about 180 ms of quiet after the last word

    def __init__(self, vad: VoiceDetector, start_frames: int = 6, end_silence_ms: int = 700,
                 max_seconds: float = 15.0, no_speech_timeout: float | None = None,
                 pre_roll_ms: int = 300) -> None:
        self.vad = vad
        self.start_frames = max(1, start_frames)
        self.end_silence_ms = end_silence_ms
        self.max_seconds = max_seconds
        self._default_timeout = no_speech_timeout
        self._pre_roll_frames = max(0, pre_roll_ms // FRAME_MS)
        self._timeout = no_speech_timeout
        self._pre: deque[bytes] = deque(maxlen=self._pre_roll_frames + 4 * self.start_frames + 10)
        self._buf: list[bytes] = []
        self._active = False
        self._heard = False
        self._count = 0              # leaky speech counter: +1 for speech, -1 for quiet
        self._cand = 0               # frames since the counter was last at zero
        self._silence = 0
        self._last_speech = 0
        self._elapsed = 0.0

    def reset(self, no_speech_timeout: float | None = None) -> None:
        """Start fresh: drop any half-heard sentence. `no_speech_timeout` (seconds) is how long to wait for someone
        to start talking before timed_out() turns True; None uses the value given when this object was made."""
        self._timeout = no_speech_timeout if no_speech_timeout is not None else self._default_timeout
        self._pre.clear()
        self._buf = []
        self._active = False
        self._heard = False
        self._count = self._cand = self._silence = self._last_speech = 0
        self._elapsed = 0.0

    def speaking(self) -> bool:
        """Someone is talking right now (a sentence has started and not yet ended)."""
        return self._active

    def timed_out(self) -> bool:
        """Nobody started talking within the timeout since reset()."""
        return (self._timeout is not None and not self._heard and not self._active
                and self._elapsed >= self._timeout)

    def feed(self, frame: bytes) -> bytes | None:
        """Give one frame. Returns the whole utterance (16 kHz mono int16 PCM) when it ends, else None."""
        seconds = len(frame) / 2 / RATE
        self._elapsed += seconds
        speech = self.vad.is_speech(frame)

        if not self._active:
            self._pre.append(frame)
            if speech:
                self._count += 1
            else:
                self._count = max(0, self._count - 1)
            self._cand = self._cand + 1 if self._count > 0 else 0
            if self._count >= self.start_frames:
                keep = list(self._pre)[-(self._cand + self._pre_roll_frames):]
                self._buf = keep
                self._active = True
                self._heard = True
                self._silence = 0
                self._last_speech = len(keep) - 1
            return None

        self._buf.append(frame)
        if speech:
            self._silence = 0
            self._last_speech = len(self._buf) - 1
        else:
            self._silence += 1
        if self._silence * seconds * 1000 >= self.end_silence_ms or len(self._buf) * seconds >= self.max_seconds:
            return self._finish()
        return None

    def _finish(self) -> bytes:
        end = min(len(self._buf), self._last_speech + 1 + self.TRAIL_FRAMES)
        pcm = b"".join(self._buf[:end])
        self._buf = []
        self._pre.clear()
        self._active = False
        self._count = self._cand = self._silence = 0
        return pcm


def pcm_to_wav(pcm: bytes, rate: int = 16000) -> bytes:
    """Wrap 16-bit mono PCM in a WAV header."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()
