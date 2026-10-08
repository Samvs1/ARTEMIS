"""Milo's little sounds, made with numpy. They follow the CHIRPS in sim/index.html.

Each chirp is a few soft sine notes: some glide up or down, some wobble slightly, and the
"marimba" ones add a short bright overtone at the start. Every chirp is scaled to the same quiet
peak (about -12 dBFS), because a sound you hear fifty times a day has to stay pleasant.
"""
from __future__ import annotations

import threading

import numpy as np

from body.vad import pcm_to_wav

RATE = 22050
PEAK = 0.25                 # about -12 dBFS
TINY = 0.0001               # the "silence" the envelopes start and end at, like the simulator

NAMES = ("happy", "curious", "surprised", "thinking", "excited", "sleepy", "wake", "listen", "bored", "poke")

# A note: (start s, frequency Hz, length s, volume, glide-to Hz or None, wobble depth or 0).
# A marimba is a note plus a short overtone at 4 times the pitch.


def _note(freq, t0, dur, vol, glide_to=None, wobble=0.0):
    return (t0, freq, dur, vol, glide_to, wobble)


def _marimba(freq, t0, vol):
    return [_note(freq, t0, 0.3, vol), _note(freq * 4, t0, 0.08, vol * 0.25)]


def _recipes() -> dict[str, list]:
    marimba, note = _marimba, _note
    return {
        "happy": marimba(659, 0, 0.5) + marimba(784, 0.1, 0.5) + [note(880, 0.2, 0.22, 0.25, 1040, 0.01)],
        "curious": [note(420, 0, 0.3, 0.4, 640, 0.015)],
        "surprised": [note(520, 0, 0.09, 0.4, 980), note(980, 0.09, 0.16, 0.35, 640)],
        "thinking": [note(210, 0, 0.55, 0.35, 236, 0.03)],
        "excited": [n for i, f in enumerate((523, 659, 784, 1047, 1319)) for n in marimba(f, i * 0.065, 0.45)],
        "sleepy": [note(380, 0, 0.8, 0.3, 230, 0.012)],
        "poke": [note(700, 0, 0.08, 0.45, 1150)] + marimba(988, 0.12, 0.4) + marimba(1175, 0.2, 0.35),
        "wake": [note(330, 0, 0.25, 0.35, 480, 0.01)] + marimba(659, 0.28, 0.45),
        "listen": marimba(880, 0, 0.35) + marimba(1175, 0.09, 0.3),
        "bored": [note(300, 0, 0.35, 0.3, 360, 0.02), note(360, 0.4, 0.35, 0.28, 300, 0.02)],
    }


# What an unknown name gets: one soft low marimba note.
_DEFAULT = _marimba(587, 0, 0.4)


def _render_note(n) -> tuple[int, np.ndarray]:
    """One note as (start sample, samples)."""
    t0, freq, dur, vol, glide_to, wobble = n
    count = int(round(dur * RATE))
    t = np.arange(count) / RATE
    if glide_to:                                     # exponential glide, as in the browser
        f = freq * (glide_to / freq) ** (t / dur)
    else:
        f = np.full(count, float(freq))
    if wobble:                                       # a 6 Hz wobble in pitch
        f = f + freq * wobble * np.sin(2 * np.pi * 6.0 * t)
    phase = 2 * np.pi * np.cumsum(f) / RATE
    attack = min(0.012, dur)                         # quick rise, long natural fade
    env = np.where(t < attack,
                   TINY * (vol / TINY) ** (t / attack),
                   vol * (TINY / vol) ** (np.clip(t - attack, 0, None) / max(dur - attack, 1e-6)))
    return int(round(t0 * RATE)), np.sin(phase) * env


def _render(notes: list) -> bytes:
    parts = [_render_note(n) for n in notes]
    length = max(start + len(x) for start, x in parts)
    mix = np.zeros(length)
    for start, x in parts:
        mix[start:start + len(x)] += x
    peak = float(np.max(np.abs(mix))) or 1.0
    pcm = np.round(mix / peak * PEAK * 32767).astype(np.int16)
    return pcm_to_wav(pcm.tobytes(), RATE)


_cache: dict[str, bytes] = {}
_lock = threading.Lock()


def chirp(name: str) -> bytes:
    """A 16-bit mono WAV of the named chirp (quiet, under 0.8 s). Unknown names give a soft default note."""
    key = (name or "").strip().lower()
    key = key if key in NAMES else "_default"
    with _lock:
        if key not in _cache:
            _cache[key] = _render(_DEFAULT if key == "_default" else _recipes()[key])
        return _cache[key]
