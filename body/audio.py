"""Milo's ears and mouth: microphone in, speaker out, and fakes for tests.

Inside the body all audio is 16 kHz, mono, 16-bit, in frames of 480 samples (30 ms) as bytes.
Playback takes 16-bit WAV bytes at any rate and any number of channels.

`sounddevice` is only needed for the real microphone and speaker. It is imported when you make
an `AudioIn` or `AudioOut`, so the rest of the body (and every test) works without it.
"""
from __future__ import annotations

import io
import queue
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

FRAME = 480          # samples per frame: 30 ms at 16 kHz
RATE = 16000
FRAME_BYTES = FRAME * 2
FRAME_SECONDS = FRAME / RATE


def log(tag: str, message: str) -> None:
    """One plain line, like the mind server: `12:30:01 hears    ...`."""
    print(time.strftime("%H:%M:%S ") + f"{tag:<8} {message}", flush=True)


class AudioError(RuntimeError):
    """Something is wrong with the microphone, the speaker or a sound file. The message says what in plain words."""


# ----------------------------------------------------------------------------
# Small helpers shared by the real and the fake versions
# ----------------------------------------------------------------------------

def _need_sounddevice(what: str):
    """Import sounddevice now (not at start-up) and explain clearly if it is missing."""
    try:
        import sounddevice as sd          # noqa: PLC0415
    except Exception as exc:              # ImportError, or PortAudio missing on the machine
        raise AudioError(
            f"I need the 'sounddevice' package to use the {what}, but it is not available ({exc}). "
            "Install it with:  pip install sounddevice   "
            "(on Raspberry Pi OS also:  sudo apt install libportaudio2)."
        ) from None
    return sd


def _find_device(sd, wanted: str, kind: str):
    """Turn "part of a name" (or a number) into a device index. "" means the default device (None)."""
    wanted = (wanted or "").strip()
    if not wanted:
        return None
    devices = sd.query_devices()
    if wanted.isdigit():
        index = int(wanted)
        if 0 <= index < len(devices):
            return index
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for index, dev in enumerate(devices):
        if wanted.lower() in str(dev["name"]).lower() and dev[key] > 0:
            return index
    names = [str(d["name"]) for d in devices if d[key] > 0]
    raise AudioError(f"I found no {kind} device with '{wanted}' in its name. "
                     f"Devices I can see: {', '.join(names) or 'none'}. Run with --list-devices to see them all.")


def to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x), -32768, 32767).astype(np.int16)


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Change the sample rate of a mono signal (linear, with a plain average first when shrinking)."""
    x = np.asarray(x, dtype=np.float32)
    if src == dst or len(x) == 0:
        return x
    if dst < src:
        width = int(round(src / dst))
        if width > 1:
            x = np.convolve(x, np.ones(width, dtype=np.float32) / width, mode="same")
    n = int(round(len(x) * dst / src))
    positions = np.arange(n) * (src / dst)
    return np.interp(positions, np.arange(len(x)), x).astype(np.float32)


def _decode_wav(wav: bytes) -> tuple[int, int, np.ndarray]:
    """Read a 16-bit PCM WAV. Returns (rate, channels, int16 array of shape (samples, channels))."""
    try:
        with wave.open(io.BytesIO(bytes(wav)), "rb") as w:
            channels, width, rate, count = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(count)
    except (wave.Error, EOFError, ValueError) as exc:
        raise AudioError(f"That is not a WAV sound I can read ({exc}).") from None
    if width != 2:
        raise AudioError(f"I can only play 16-bit WAV sounds, this one has {width * 8} bits per sample.")
    if channels < 1 or rate < 1:
        raise AudioError("That WAV sound has no channels or no sample rate.")
    data = np.frombuffer(raw, dtype="<i2")
    data = data[: len(data) // channels * channels].reshape(-1, channels)
    return rate, channels, data


def _mono_float(data: np.ndarray) -> np.ndarray:
    """(samples, channels) int16 -> mono float32 in int16 units."""
    if data.shape[1] == 1:
        return data[:, 0].astype(np.float32)
    return data.astype(np.float32).mean(axis=1)


def wav_info(wav: bytes) -> tuple[int, int, int]:
    """(sample rate, channels, number of samples per channel) of a WAV."""
    rate, channels, data = _decode_wav(wav)
    return rate, channels, int(data.shape[0])


def _wav_to_frames_pcm(wav_or_path) -> bytes:
    """A WAV (bytes or a file path) as 16 kHz mono int16 PCM bytes."""
    if isinstance(wav_or_path, (bytes, bytearray, memoryview)):
        wav = bytes(wav_or_path)
    else:
        try:
            wav = Path(wav_or_path).read_bytes()
        except OSError as exc:
            raise AudioError(f"I could not read the sound file {wav_or_path}: {exc}") from None
    rate, _, data = _decode_wav(wav)
    return to_int16(resample(_mono_float(data), rate, RATE)).tobytes()


class _Level:
    """Turns chunks of sound into a smooth 0..1 loudness for the mouth. A normal voice lands around 0.3 to 0.8."""

    def __init__(self) -> None:
        self.value = 0.0

    def update(self, chunk: np.ndarray) -> float:
        """`chunk` is float in int16 units."""
        if len(chunk) == 0:
            rms = 0.0
        else:
            rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64)))) / 32768.0
        raw = 0.0 if rms < 0.004 else min(1.0, (rms / 0.2) ** 0.6)
        self.value += (raw - self.value) * (0.6 if raw > self.value else 0.3)
        return min(1.0, max(0.0, self.value))


# ----------------------------------------------------------------------------
# Microphone
# ----------------------------------------------------------------------------

class _StreamResampler:
    """Turns a microphone's native stream (any rate, 1 or more channels) into 16 kHz mono, chunk by chunk."""

    def __init__(self, src_rate: int, channels: int) -> None:
        self.channels = max(1, channels)
        self.step = src_rate / RATE
        self.width = max(1, int(round(self.step))) if self.step > 1 else 1
        self.kernel = np.ones(self.width, dtype=np.float32) / self.width
        self.tail = np.zeros(0, dtype=np.float32)
        self.pos = 0.0

    def process(self, raw: bytes) -> bytes:
        x = np.frombuffer(raw, dtype=np.int16)
        x = x[: len(x) // self.channels * self.channels]
        x = x.reshape(-1, self.channels).mean(axis=1) if self.channels > 1 else x
        data = np.concatenate([self.tail, x.astype(np.float32)])
        if len(data) < self.width + 1:
            self.tail = data
            return b""
        filtered = np.convolve(data, self.kernel, mode="valid") if self.width > 1 else data
        last = len(filtered) - 1
        if self.pos > last:
            self.tail = data
            return b""
        n = int((last - self.pos) // self.step) + 1
        out = np.interp(self.pos + np.arange(n) * self.step, np.arange(len(filtered)), filtered)
        nxt = self.pos + n * self.step
        cut = min(int(nxt), len(data))
        self.tail = data[cut:]
        self.pos = nxt - cut
        return to_int16(out).tobytes()


class AudioIn:
    """The real microphone (needs sounddevice). `frames()` gives endless 30 ms frames of 16 kHz mono int16."""

    def __init__(self, device: str = "") -> None:
        sd = _need_sounddevice("microphone")
        self._closed = threading.Event()
        self._queue: queue.Queue = queue.Queue(maxsize=300)       # about 9 seconds, then the oldest is dropped
        self.overflows = 0
        index = _find_device(sd, device, "input")
        self._stream, self.rate, self.channels = self._open(sd, index)
        try:
            name = sd.query_devices(index, "input")["name"]
        except Exception:
            name = "default"
        log("audio", f"microphone: {name} at {self.rate} Hz" + ("" if self.rate == RATE else " (I will resample to 16 kHz)"))
        self._resampler = None if (self.rate == RATE and self.channels == 1) else _StreamResampler(self.rate, self.channels)

    def _open(self, sd, index):
        """Try 16 kHz mono first; if the device cannot, use its own rate and resample."""
        try:
            native = int(sd.query_devices(index, "input")["default_samplerate"])
        except Exception:
            native = 48000
        tries = [(RATE, 1, FRAME)]
        if native != RATE:
            tries += [(native, 1, 0), (native, 2, 0)]
        else:
            tries += [(RATE, 2, FRAME)]
        last_error = None
        for rate, channels, block in tries:
            try:
                stream = sd.RawInputStream(samplerate=rate, blocksize=block, device=index, channels=channels,
                                           dtype="int16", callback=self._on_audio)
                stream.start()
                return stream, rate, channels
            except Exception as exc:
                last_error = exc
        raise AudioError(f"I could not open the microphone ({last_error}). "
                         "Check that it is plugged in, or choose one with MILO_INPUT_DEVICE (see --list-devices).")

    def _on_audio(self, indata, frames, time_info, status) -> None:
        """Called by the sound system on its own thread: just hand the bytes over."""
        if status:
            self.overflows += 1
        chunk = bytes(indata)
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(chunk)
            except (queue.Empty, queue.Full):
                pass

    def frames(self) -> Iterator[bytes]:
        """Endless 30 ms frames. Blocks until sound arrives. Ends after close()."""
        try:
            while True:                                  # forget anything that piled up before we started listening
                self._queue.get_nowait()
        except queue.Empty:
            pass
        pending = bytearray()
        while not self._closed.is_set():
            try:
                chunk = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if chunk is None:
                break
            if self._resampler is not None:
                chunk = self._resampler.process(chunk)
            pending += chunk
            while len(pending) >= FRAME_BYTES:
                yield bytes(pending[:FRAME_BYTES])
                del pending[:FRAME_BYTES]

    def close(self) -> None:
        self._closed.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class FakeAudioIn:
    """A pretend microphone for tests and --fake-audio: WAV files (paths or bytes) one after another, with silence between."""

    def __init__(self, wav_paths_or_bytes: list, gap_seconds: float = 1.0, realtime: bool = False) -> None:
        self._clips = [_wav_to_frames_pcm(item) for item in wav_paths_or_bytes]
        self.gap_frames = max(0, int(round(gap_seconds * RATE / FRAME)))
        self.realtime = realtime
        self._closed = threading.Event()

    def frames(self) -> Iterator[bytes]:
        """Silence, clip, silence, clip, ..., silence, then the stream ends."""
        silence = bytes(FRAME_BYTES)
        for clip in self._clips:
            for _ in range(self.gap_frames):
                if not self._tick():
                    return
                yield silence
            for start in range(0, len(clip), FRAME_BYTES):
                if not self._tick():
                    return
                yield clip[start:start + FRAME_BYTES].ljust(FRAME_BYTES, b"\x00")
        for _ in range(self.gap_frames):
            if not self._tick():
                return
            yield silence

    def _tick(self) -> bool:
        if self._closed.is_set():
            return False
        if self.realtime:
            time.sleep(FRAME_SECONDS)
        return True

    def close(self) -> None:
        self._closed.set()


# ----------------------------------------------------------------------------
# Speaker
# ----------------------------------------------------------------------------

class AudioOut:
    """The real speaker (needs sounddevice). `play()` blocks until the sound is done or `stop()` is called."""

    def __init__(self, device: str = "") -> None:
        self._sd = _need_sounddevice("speaker")
        self._index = _find_device(self._sd, device, "output")
        self._stop = threading.Event()
        try:
            info = self._sd.query_devices(self._index, "output")
            self._rate = int(info["default_samplerate"])
            name = info["name"]
        except Exception as exc:
            raise AudioError(f"I could not find a speaker ({exc}). Choose one with MILO_OUTPUT_DEVICE (see --list-devices).") from None
        log("audio", f"speaker: {name} at {self._rate} Hz")

    def _open(self):
        last_error = None
        for channels in (1, 2):
            try:
                stream = self._sd.RawOutputStream(samplerate=self._rate, device=self._index, channels=channels, dtype="int16")
                stream.start()
                return stream, channels
            except Exception as exc:
                last_error = exc
        raise AudioError(f"I could not open the speaker ({last_error}). Check the output device (see --list-devices).")

    def play(self, wav: bytes, on_level: Callable[[float], None] | None = None) -> bool:
        """Play a WAV. Calls on_level(0..1) about 30 times a second, and on_level(0) at the end.
        True if it played to the end, False if stop() cut it short."""
        rate, _, data = _decode_wav(wav)
        samples = resample(_mono_float(data), rate, self._rate)
        self._stop.clear()
        stream, channels = self._open()
        chunk = max(1, int(self._rate * FRAME_SECONDS))
        level = _Level()
        finished = True
        try:
            for start in range(0, len(samples), chunk):
                if self._stop.is_set():
                    finished = False
                    break
                piece = samples[start:start + chunk]
                if on_level:
                    on_level(level.update(piece))
                self._write(stream, to_int16(piece), channels)
            if finished:
                # Push a little silence through so the end of the sound is really heard, while still listening for stop().
                silence = np.zeros(chunk, dtype=np.int16)
                for _ in range(int(min(0.5, self._latency(stream) + 0.03) / FRAME_SECONDS) + 1):
                    if self._stop.is_set():
                        finished = False
                        break
                    self._write(stream, silence, channels)
        finally:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass
            if on_level:
                on_level(0.0)
        return finished

    @staticmethod
    def _latency(stream) -> float:
        try:
            lat = stream.latency
            return float(max(lat) if isinstance(lat, (tuple, list)) else lat)
        except Exception:
            return 0.1

    @staticmethod
    def _write(stream, samples: np.ndarray, channels: int) -> None:
        if channels == 2:
            samples = np.repeat(samples[:, None], 2, axis=1)
        try:
            stream.write(samples.tobytes())
        except Exception as exc:
            raise AudioError(f"The speaker stopped working while playing ({exc}).") from None

    def stop(self) -> None:
        """Cut the sound now. Safe from any thread; play() returns False within about 30 to 100 ms."""
        self._stop.set()

    def close(self) -> None:
        self._stop.set()


class FakeAudioOut:
    """A pretend speaker: remembers every WAV it was given in `.played` and plays instantly (or in real time)."""

    def __init__(self, device: str = "", realtime: bool = False) -> None:
        self.played: list[bytes] = []
        self.realtime = realtime
        self._stop = threading.Event()

    def play(self, wav: bytes, on_level: Callable[[float], None] | None = None) -> bool:
        rate, _, data = _decode_wav(wav)                 # a broken WAV fails here, like the real speaker
        self._stop.clear()
        self.played.append(bytes(wav))
        samples = _mono_float(data)
        chunk = max(1, int(rate * FRAME_SECONDS))
        starts = list(range(0, len(samples), chunk))
        level = _Level()
        finished = True
        if self.realtime:
            for start in starts:
                if self._stop.is_set():
                    finished = False
                    break
                if on_level:
                    on_level(level.update(samples[start:start + chunk]))
                time.sleep(FRAME_SECONDS)
        elif on_level and starts:
            picks = sorted({starts[i * (len(starts) - 1) // 3] for i in range(4)}) if len(starts) > 1 else starts
            for start in picks:
                on_level(level.update(samples[start:start + chunk]))
        if on_level:
            on_level(0.0)
        return finished

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._stop.set()


def list_devices() -> str:
    """A readable list of the sound devices, for --list-devices."""
    try:
        sd = _need_sounddevice("sound devices list")
    except AudioError as exc:
        return str(exc)
    try:
        devices = sd.query_devices()
        default_in, default_out = sd.default.device
    except Exception as exc:
        return f"I could not ask the sound system for its devices ({exc})."
    lines = ["Sound devices (choose one with MILO_INPUT_DEVICE / MILO_OUTPUT_DEVICE using part of its name):"]
    for index, dev in enumerate(devices):
        marks = []
        if dev["max_input_channels"] > 0:
            marks.append("microphone" + (" (default)" if index == default_in else ""))
        if dev["max_output_channels"] > 0:
            marks.append("speaker" + (" (default)" if index == default_out else ""))
        lines.append(f"  {index}: {dev['name']}  [{', '.join(marks) or 'neither'}]  {dev['default_samplerate']:.0f} Hz")
    if not devices:
        lines.append("  none found")
    return "\n".join(lines)
