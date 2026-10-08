"""The conversation: wake, listen, think, speak, keep listening a moment, rest.

One thread reads the microphone and drives the states. The thinking (speech to text and the
mind's reply) runs in a worker thread, and everything Milo plays goes through one speaker
thread, so the microphone is never starved and an interruption can stop everything at once.

    idle --wake--> listening --utterance--> thinking --first sentence--> speaking --done--> listening (window) --silence--> idle
                      ^                                                       |
                      +---------------- you talk over Milo (barge-in) --------+
"""
from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

from body.chirps import chirp
from body.config import log
from body.mind_client import MindError
from body.stt import SttError
from body.vad import Utterances, VoiceDetector, pcm_to_wav

IDLE, LISTENING, THINKING, SPEAKING = "idle", "listening", "thinking", "speaking"


class _Stop:
    """Marks the end of one reply in the speaker queue."""

    def __init__(self, gen: int) -> None:
        self.gen = gen


class Conversation:
    def __init__(self, settings, mic, speaker, wake, stt, mind, face, *,
                 vad: VoiceDetector | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.mic, self.speaker, self.wake = mic, speaker, wake
        self.stt, self.mind, self.face = stt, mind, face
        self.clock = clock
        vad = vad or VoiceDetector()
        self.listener = Utterances(vad)
        self.barge = Utterances(vad, start_frames=8)    # a little stricter: Milo's own voice must not trigger it
        self.state = IDLE
        self.mood = "calm"
        self.turns = 0
        self._gen = 0                                   # bumped on every interruption; stale work checks it and stops
        self._lock = threading.RLock()
        self._sound: queue.Queue = queue.Queue()
        self._voices = ThreadPoolExecutor(max_workers=2, thread_name_prefix="voice")
        self._worker: threading.Thread | None = None
        self._speaker_thread = threading.Thread(target=self._speaker_loop, name="speaker", daemon=True)
        self._speaker_thread.start()

    # ---------------------------------------------------------------- the microphone loop
    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        self._set_state(IDLE)
        log("body", "ready. " + ("Say the wake word." if getattr(self.wake, "available", True) else "Press Enter to talk."))
        for frame in self.mic.frames():
            if stop.is_set():
                break
            self.on_frame(frame)
        self.wait_until_idle(timeout=60)

    def on_frame(self, frame: bytes) -> None:
        with self._lock:
            state = self.state
        if state == IDLE:
            if self.wake.feed(frame):
                log("hears", "wake word")
                self.start_listening(chime=True)
        elif state == LISTENING:
            utt = self.listener.feed(frame)
            if utt:
                self._think(utt)
            elif self.listener.timed_out():
                log("body", "nothing more heard, resting")
                self._set_state(IDLE)
                self.wake.reset()
        elif self.settings.barge_in:                    # thinking or speaking
            self.barge.feed(frame)
            if self.barge.speaking():
                log("hears", "you talked over Milo: stopping")
                self.interrupt()
                self.start_listening(chime=False)

    # ---------------------------------------------------------------- states
    def start_listening(self, chime: bool) -> None:
        with self._lock:
            self.listener.reset(no_speech_timeout=self.settings.window_seconds)
            self._set_state(LISTENING)
        if chime:
            self._play(chirp("listen"))

    def interrupt(self) -> None:
        with self._lock:
            self._gen += 1
        self.mind.cancel()
        self._clear_sound()
        self.speaker.stop()
        self.face.send({"type": "mouth", "level": 0.0})

    def busy(self) -> bool:
        """Thinking or speaking (or something still queued for the speaker)."""
        with self._lock:
            working = self.state in (THINKING, SPEAKING) or bool(self._worker and self._worker.is_alive())
        return working or not self._sound.empty()

    def wait_until_idle(self, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.busy():
                return True
            time.sleep(0.05)
        return False

    def _set_state(self, name: str) -> None:
        with self._lock:
            self.state = name
        self.face.send({"type": "state", "name": name})

    # ---------------------------------------------------------------- thinking
    def _think(self, pcm: bytes) -> None:
        with self._lock:
            self._set_state(THINKING)
            gen = self._gen
            self.barge.reset()
        self._play(chirp("thinking"))
        self._worker = threading.Thread(target=self._reply, args=(pcm, gen), name="reply", daemon=True)
        self._worker.start()

    def _stale(self, gen: int) -> bool:
        with self._lock:
            return gen != self._gen

    def _reply(self, pcm: bytes, gen: int) -> None:
        try:
            text, used = self.stt.transcribe(pcm_to_wav(pcm))
        except SttError as e:
            return self._fail(gen, f"could not understand the audio: {e}")
        text = text.strip()
        if self._stale(gen):
            return
        if not text:
            log("hears", "(nothing understood)")
            return self._back_to_listening(gen)
        log("hears", f"“{text}”  ({used})")
        self.face.send({"type": "caption", "text": text})

        started = time.monotonic()
        spoke = False
        try:
            for ev in self.mind.chat(text, state=self.body_state()):
                if self._stale(gen):
                    return
                kind = ev.get("type")
                if kind == "error":
                    return self._fail(gen, ev.get("message") or "the mind had a problem")
                if kind == "say":
                    if not spoke:
                        log("mind", f"first sentence after {int((time.monotonic() - started) * 1000)} ms")
                    spoke = True
                    self._sound.put(("voice", gen, self._voices.submit(self.mind.tts, ev["text"], "wav"), ev["text"]))
                elif kind in ("emote", "look", "sound"):
                    if kind == "emote":
                        self.mood = ev.get("name") or self.mood
                    if spoke:
                        self._sound.put(("cue", gen, ev, None))     # in order with the speech
                    else:
                        self._cue(ev)                               # before the first words: at once
        except MindError as e:
            return self._fail(gen, str(e))
        if not spoke:
            return self._back_to_listening(gen)
        self.turns += 1
        self._sound.put(_Stop(gen))

    def _fail(self, gen: int, why: str) -> None:
        if self._stale(gen):
            return
        log("body", f"problem: {why}")
        self.face.send({"type": "state", "name": "offline"})
        self._play(chirp("sleepy"))
        with self._lock:
            self.state = IDLE
        self.wake.reset()

    def _back_to_listening(self, gen: int) -> None:
        if not self._stale(gen):
            self.start_listening(chime=False)

    def body_state(self) -> dict:
        return {"mood": self.mood, "lights": True, "local_time": time.strftime("%A %d %B %Y, %H:%M")}

    def _cue(self, ev: dict) -> None:
        if ev["type"] == "sound":
            self._play(chirp(ev.get("name") or ""))
        else:
            self.face.send(ev)

    # ---------------------------------------------------------------- the speaker
    def _play(self, wav: bytes) -> None:
        self._sound.put(("chirp", self._gen, wav, None))

    def _clear_sound(self) -> None:
        try:
            while True:
                self._sound.get_nowait()
        except queue.Empty:
            pass

    def _speaker_loop(self) -> None:
        while True:
            item = self._sound.get()
            if isinstance(item, _Stop):
                if not self._stale(item.gen):
                    self.face.send({"type": "mouth", "level": 0.0})
                    self.start_listening(chime=False)        # the conversation window
                continue
            kind, gen, payload, text = item
            if self._stale(gen):
                continue
            if kind == "cue":
                self._cue(payload)
            elif kind == "chirp":
                self.speaker.play(payload)
            elif kind == "voice":
                try:
                    wav = payload.result() if isinstance(payload, Future) else payload
                except MindError as e:
                    self._fail(gen, f"no voice: {e}")
                    self._clear_sound()
                    continue
                if self._stale(gen):
                    continue
                if self.state != SPEAKING:
                    self._set_state(SPEAKING)
                self.speaker.play(wav, on_level=lambda lv: self.face.send({"type": "mouth", "level": round(lv, 3)}))

    def close(self) -> None:
        self.interrupt()
        self._voices.shutdown(wait=False, cancel_futures=True)
