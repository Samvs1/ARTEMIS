"""Waking Arty up: the wake word, or a keypress when there is no wake word model.

`WakeWord` uses openWakeWord if it is installed. If the package or the model is missing it does
not crash: `available` is False and the reason is logged, so the caller can use `PushToTalk`.
"""
from __future__ import annotations

import sys
import threading
import time

import numpy as np

from body.audio import log

CHUNK = 1280                     # openWakeWord wants 80 ms (1280 samples) at a time
CHUNK_BYTES = CHUNK * 2
COOLDOWN_SECONDS = 1.5           # after a hit, ignore the next ones for a moment


class WakeWord:
    """Listens for a wake word. `available` is False when openwakeword or the model is missing."""

    def __init__(self, model: str, threshold: float = 0.5) -> None:
        self.model_name = (model or "").strip()
        self.threshold = threshold
        self.available = False
        self.reason = ""
        self._model = None
        self._buffer = bytearray()
        self._quiet_until = 0.0
        if not self.model_name:
            self.reason = "no wake word is set"
        else:
            self._load()
        if self.available:
            log("wake", f"listening for the wake word '{self.model_name}'")
        else:
            log("wake", f"wake word is off: {self.reason}")

    def _load(self) -> None:
        try:
            import openwakeword                          # noqa: PLC0415
        except Exception as exc:
            self.reason = f"the 'openwakeword' package is not installed ({exc}); install it with: pip install openwakeword"
            return
        name = self.model_name
        is_file = name.lower().endswith((".onnx", ".tflite"))
        framework = "tflite" if name.lower().endswith(".tflite") else "onnx"
        error = None
        for attempt in (1, 2):
            try:
                self._model = openwakeword.Model(wakeword_models=[name], inference_framework=framework)
                self.available = True
                return
            except Exception as exc:
                error = exc
                if attempt == 2 or is_file or not self._download(openwakeword):
                    break
        self.reason = f"I could not load the wake word model '{name}' ({error})"

    @staticmethod
    def _download(openwakeword) -> bool:
        """Ask openWakeWord to fetch its ready-made models, if this version has the helper. True if it ran."""
        helper = getattr(getattr(openwakeword, "utils", None), "download_models", None)
        if helper is None:
            return False
        log("wake", "downloading the wake word models (once)...")
        try:
            try:
                helper()
            except TypeError:
                helper([])
            return True
        except Exception as exc:
            log("wake", f"the download did not work ({exc})")
            return False

    def reset(self) -> None:
        """Forget the sound heard so far, so what was just said cannot trigger it again."""
        self._buffer.clear()
        self._quiet_until = 0.0
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            try:
                reset()
            except Exception:
                pass

    def feed(self, frame: bytes) -> bool:
        """Give one 30 ms frame. True once when the wake word is heard."""
        if not self.available:
            return False
        self._buffer += frame
        heard = False
        while len(self._buffer) >= CHUNK_BYTES:
            chunk = np.frombuffer(bytes(self._buffer[:CHUNK_BYTES]), dtype=np.int16)
            del self._buffer[:CHUNK_BYTES]
            try:
                scores = self._model.predict(chunk)
                best = max(scores.values()) if scores else 0.0
            except Exception as exc:
                self.available = False
                self.reason = f"the wake word model failed ({exc})"
                log("wake", f"wake word is off: {self.reason}")
                return False
            if best >= self.threshold and time.monotonic() >= self._quiet_until:
                heard = True
                self._quiet_until = time.monotonic() + COOLDOWN_SECONDS
                reset = getattr(self._model, "reset", None)
                if callable(reset):
                    try:
                        reset()
                    except Exception:
                        pass
        return heard


class PushToTalk:
    """The fallback "wake word": press Enter in the terminal, or call trigger() from code."""

    available = True
    push_to_talk = True

    def __init__(self, use_stdin: bool = True) -> None:
        self._lock = threading.Lock()
        self._flag = False
        if use_stdin and sys.stdin is not None:
            threading.Thread(target=self._read_keys, name="push-to-talk", daemon=True).start()
            log("wake", "push to talk: press Enter to talk to Arty")

    def _read_keys(self) -> None:
        try:
            for _ in sys.stdin:
                self.trigger()
        except (OSError, ValueError):
            pass                       # no keyboard (for example when run as a service); trigger() still works

    def trigger(self) -> None:
        with self._lock:
            self._flag = True

    def reset(self) -> None:
        with self._lock:
            self._flag = False

    def feed(self, frame: bytes) -> bool:
        """True once for every trigger."""
        with self._lock:
            hit, self._flag = self._flag, False
        return hit
