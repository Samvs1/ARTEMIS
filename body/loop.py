"""The conversation: wake, listen, think, speak, keep listening a moment, rest.

One thread reads the microphone and drives the states. The thinking (speech to text and the
mind's reply) runs in a worker thread, and everything Arty plays goes through one speaker
thread, so the microphone is never starved and an interruption can stop everything at once.

    idle --wake--> listening --utterance--> thinking --first sentence--> speaking --done--> listening (window) --silence--> idle
                      ^                                                       |
                      +---------------- you talk over Arty (barge-in) --------+

Three more things share the same loop:

* Timers and the focus block. The mind writes them into the reply stream ({"type": "timer", ...} and
  {"type": "focus", ...}); the Scheduler keeps them and every microphone frame asks it what has ended.
  When one has, and Arty is idle, the body starts a reply by itself (no wake word, no speech to text):
  the event "timer_done" (detail: the label) or "focus_break" (detail: the minutes). When Arty is busy the
  event waits until the talk is over. During a focus block the resting face is "focus" instead of "idle".
* No mind. If the mind does not answer, the body keeps running with the "offline" face. The wake word still
  chirps (listen, then sleepy) and every 10 seconds a background check asks the mind again; when it answers,
  the face goes back to resting.
* Nothing in here sleeps on the clock: pass `clock` to make time stand still or jump in tests.
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

MAX_TIMERS = 3
LONGEST_SECONDS = 12 * 3600                         # no timer or focus block is longer than this
MIND_CHECK_SECONDS = 10.0                           # how often to ask a silent mind whether it is back
UNANSWERED_WAKE_GAP = 2.0                           # seconds before the wake word gets another sleepy chirp while offline
# What to play when a timer or focus block ends and the mind cannot put it into words.
EVENT_CHIMES = {"timer_done": "excited", "focus_break": "happy"}


def span(seconds: float) -> str:
    """Seconds in plain words for the log: '90 seconds', '10 minutes', '1 hour 30 minutes'."""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{n} {unit}{'' if n == 1 else 's'}" for n, unit in ((hours, "hour"), (minutes, "minute"), (secs, "second")) if n]
    return " ".join(parts) or "0 seconds"


class Scheduler:
    """The timers and the focus block. It only ever looks at the clock it is given, so nothing here sleeps.

    due() hands back what has ended since the last call, once, soonest first, as (event, detail) pairs:
    ("timer_done", label) and ("focus_break", minutes).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._timers: list[tuple[float, str]] = []      # (when it ends, label), the one set first at the front
        self._focus_end: float | None = None
        self._focus_minutes = 0

    @staticmethod
    def _number(value, longest: float) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return min(float(value), longest) if value == value and value > 0 else None     # not NaN, not zero or less

    def add_timer(self, seconds, label: str = "") -> dict | None:
        """Start a timer. A fourth one replaces the oldest. Returns what was set (and what it replaced), or None if unusable."""
        length = self._number(seconds, LONGEST_SECONDS)
        if length is None:
            return None
        label = " ".join(str(label or "").split())[:40]
        with self._lock:
            replaced = self._timers.pop(0)[1] if len(self._timers) >= MAX_TIMERS else None
            self._timers.append((self.clock() + length, label))
        return {"seconds": length, "label": label, "replaced": replaced}

    def cancel_timers(self) -> int:
        with self._lock:
            count, self._timers = len(self._timers), []
        return count

    def timers(self) -> list[dict]:
        """What is running: label and seconds left, in the order they were set."""
        now = self.clock()
        with self._lock:
            return [{"label": label, "left": max(0.0, end - now)} for end, label in self._timers]

    def start_focus(self, minutes) -> int | None:
        """Start (or restart) the focus block. Returns the minutes, or None if unusable."""
        length = self._number(minutes, LONGEST_SECONDS / 60)
        if length is None:
            return None
        with self._lock:
            self._focus_minutes = max(1, int(round(length)))
            self._focus_end = self.clock() + self._focus_minutes * 60
            return self._focus_minutes

    def stop_focus(self) -> bool:
        """End the focus block early, without an event. True if one was running."""
        with self._lock:
            was, self._focus_end = self._focus_end is not None, None
        return was

    def focus_active(self) -> bool:
        with self._lock:
            return self._focus_end is not None

    def due(self) -> list[tuple[str, str]]:
        now = self.clock()
        with self._lock:
            ended: list[tuple[float, str, str]] = [(end, "timer_done", label) for end, label in self._timers if end <= now]
            self._timers = [(end, label) for end, label in self._timers if end > now]
            if self._focus_end is not None and self._focus_end <= now:
                ended.append((self._focus_end, "focus_break", str(self._focus_minutes)))
                self._focus_end = None
        ended.sort(key=lambda item: item[0])
        return [(event, detail) for _, event, detail in ended]


class _Stop:
    """Marks the end of one reply in the speaker queue."""

    def __init__(self, gen: int) -> None:
        self.gen = gen


class Conversation:
    def __init__(self, settings, mic, speaker, wake, stt, mind, face, *,
                 vad: VoiceDetector | None = None, clock: Callable[[], float] = time.monotonic,
                 offline: bool = False) -> None:
        self.settings = settings
        self.mic, self.speaker, self.wake = mic, speaker, wake
        self.stt, self.mind, self.face = stt, mind, face
        self.clock = clock
        vad = vad or VoiceDetector()
        self.listener = Utterances(vad)
        self.barge = Utterances(vad, start_frames=8)    # a little stricter: Arty's own voice must not trigger it
        self.state = IDLE
        self.mood = "calm"
        self.turns = 0
        self.scheduler = Scheduler(clock)
        self.offline = offline                          # the mind does not answer: run on without it
        self._pending: list[tuple[str, str]] = []       # ended timers and focus blocks waiting for Arty to be idle
        self._next_check = clock() + MIND_CHECK_SECONDS
        self._probing = False
        self._probe_thread: threading.Thread | None = None
        self._last_unanswered_wake: float | None = None
        self._gen = 0                                   # bumped on every interruption; stale work checks it and stops
        self._timing: dict[int, dict[str, float]] = {}  # per spoken turn: when speech ended, was cut, understood, answered
        self._lock = threading.RLock()
        self._sound: queue.Queue = queue.Queue()
        self._voices = ThreadPoolExecutor(max_workers=2, thread_name_prefix="voice")
        self._worker: threading.Thread | None = None
        self._speaker_thread = threading.Thread(target=self._speaker_loop, name="speaker", daemon=True)
        self._speaker_thread.start()

    # ---------------------------------------------------------------- the microphone loop
    def begin(self) -> None:
        """Show the resting face and say that Arty is ready (run() does this first)."""
        self._set_state(IDLE)
        if self.offline:
            log("body", "ready, but without the mind: showing the offline face and checking again every 10 seconds.")
        else:
            log("body", "ready. " + ("Press Enter to talk." if getattr(self.wake, "push_to_talk", False) or not getattr(self.wake, "available", True)
                                  else "Say the wake word."))

    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        self.begin()
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
                if self.offline:
                    self._wake_without_mind()
                else:
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
                log("hears", "you talked over Arty: stopping")
                self.interrupt()
                self.start_listening(chime=False)
        self.tick()                                     # after the wake word, so the person always comes first

    def tick(self) -> None:
        """Look at the clock: have a timer or the focus block ended, is it time to ask a silent mind again?

        Called for every microphone frame. It never waits, so it is fine on the microphone thread."""
        for event, detail in self.scheduler.due():
            if event == "timer_done":
                log("body", f"the timer '{detail}' has ended" if detail else "a timer has ended")
            else:
                log("body", f"the {detail}-minute focus block has ended")
            with self._lock:
                self._pending.append((event, detail))
        self._start_pending()
        self._check_mind()

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
            shown = self._resting_face() if name == IDLE else name
        self.face.send({"type": "state", "name": shown})

    def _resting_face(self) -> str:
        """What the face shows while Arty waits: offline, focus or idle."""
        if self.offline:
            return "offline"
        return "focus" if self.scheduler.focus_active() else "idle"

    def _refresh_resting_face(self) -> None:
        """The reason for the resting face changed (focus started or ended, the mind came back): show it if Arty is resting."""
        with self._lock:
            if self.state != IDLE:
                return
            shown = self._resting_face()
        self.face.send({"type": "state", "name": shown})

    # ---------------------------------------------------------------- timers and focus
    def _schedule(self, ev: dict) -> None:
        """Apply a timer or focus stage direction from the reply stream."""
        if ev["type"] == "timer":
            if ev.get("cancel"):
                count = self.scheduler.cancel_timers()
                with self._lock:                        # one that ended but was not announced yet is cancelled too
                    waiting = len(self._pending)
                    self._pending = [p for p in self._pending if p[0] != "timer_done"]
                    waiting -= len(self._pending)
                log("body", "timers cancelled" if count or waiting else "no timers to cancel")
                return
            started = self.scheduler.add_timer(ev.get("seconds"), ev.get("label") or "")
            if started is None:
                log("body", "ignored a timer without a usable length")
                return
            name = f" '{started['label']}'" if started["label"] else ""
            replaced = f" (the oldest, '{started['replaced']}', is replaced)" if started["replaced"] is not None else ""
            log("body", f"timer{name} set for {span(started['seconds'])}{replaced}")
        else:
            if ev.get("stop"):
                log("body", "focus ended early" if self.scheduler.stop_focus() else "no focus block to end")
            else:
                minutes = self.scheduler.start_focus(ev.get("minutes"))
                if minutes is None:
                    log("body", "ignored a focus block without a usable length")
                    return
                log("body", f"focus block of {span(minutes * 60)} started")
            self._refresh_resting_face()

    def _start_pending(self) -> None:
        """Start the reply for an ended timer or focus block, if Arty is idle. Otherwise it keeps waiting."""
        with self._lock:
            if not self._pending or self.state != IDLE or self.busy():
                return
            event, detail = self._pending.pop(0)
            if self.offline:
                self._play(chirp(EVENT_CHIMES[event]))      # no mind to put it into words: at least ring
                log("body", "no mind to ask, so just a chime")
            else:
                self._set_state(THINKING)
                gen = self._gen
                self.barge.reset()
                self._worker = threading.Thread(target=self._event_reply, args=(event, detail, gen), name="event", daemon=True)
                self._worker.start()

    # ---------------------------------------------------------------- the mind is away
    def _go_offline(self) -> None:
        """Show that the mind is away (once: the log only tells about the change) and start checking for it."""
        with self._lock:
            already, self.offline = self.offline, True
            if not already:
                self._next_check = self.clock() + MIND_CHECK_SECONDS
        if not already:
            log("body", f"offline. Arty keeps running and looks again every {int(MIND_CHECK_SECONDS)} seconds.")

    def _wake_without_mind(self) -> None:
        """The wake word while offline: the usual chirp, then a sleepy one, and no thinking."""
        now = self.clock()
        if self._last_unanswered_wake is not None and now - self._last_unanswered_wake < UNANSWERED_WAKE_GAP:
            return                                      # one wake word, one chirp (a recording or a jumpy detector repeats itself)
        self._last_unanswered_wake = now
        log("body", "the mind is offline, so there is no answer")
        self._play(chirp("listen"))
        self._play(chirp("sleepy"))
        self.face.send({"type": "state", "name": "offline"})
        self.wake.reset()
        self._check_mind(force=True)                    # it may be back already

    def _check_mind(self, force: bool = False) -> None:
        """While offline, ask the mind whether it is back: every 10 seconds, in the background, one check at a time."""
        now = self.clock()
        with self._lock:
            if not self.offline or self._probing or (now < self._next_check and not force):
                return
            self._probing = True
            self._next_check = now + MIND_CHECK_SECONDS
            self._probe_thread = threading.Thread(target=self._probe, name="mind-check", daemon=True)
            self._probe_thread.start()

    def _probe(self) -> None:
        try:
            answer = self.mind.health()
        except Exception:                               # a check must never take the body down
            answer = None
        with self._lock:
            self._probing = False
            back = bool(answer) and self.offline
            if back:
                self.offline = False
        if back:
            log("body", "the mind answers again")
            self._refresh_resting_face()

    # ---------------------------------------------------------------- thinking
    def _think(self, pcm: bytes) -> None:
        with self._lock:
            self._set_state(THINKING)
            gen = self._gen
            self.barge.reset()
            closed = time.monotonic()
            self._timing = {gen: {"end": closed - self.listener.end_silence_ms / 1000, "closed": closed}}
        if self.settings.thinking_chirp:
            self._play(chirp("thinking"))
        self._worker = threading.Thread(target=self._reply, args=(pcm, gen), name="reply", daemon=True)
        self._worker.start()

    def _mark(self, gen: int, step: str) -> None:
        with self._lock:
            if gen in self._timing:
                self._timing[gen][step] = time.monotonic()

    def _log_timing(self, gen: int) -> None:
        """Once per spoken turn, as its first words play: where the time from end of speech went."""
        with self._lock:
            t = self._timing.pop(gen, None)
        if not t or "stt" not in t or "say" not in t:
            return
        now = time.monotonic()
        ms = lambda a, b: int(round((b - a) * 1000))
        log("timing", f"end of speech -> first voice {ms(t['end'], now)} ms (silence wait {ms(t['end'], t['closed'])}, "
                      f"speech to text {ms(t['closed'], t['stt'])}, mind first sentence {ms(t['stt'], t['say'])}, "
                      f"voice {ms(t['say'], now)})")

    def _stale(self, gen: int) -> bool:
        with self._lock:
            return gen != self._gen

    def _reply(self, pcm: bytes, gen: int) -> None:
        """Something the person said: understand it, then answer."""
        try:
            text, used = self.stt.transcribe(pcm_to_wav(pcm))
        except SttError as e:
            return self._fail(gen, f"could not understand the audio: {e}")
        text = text.strip()
        self._mark(gen, "stt")
        if self._stale(gen):
            return
        if not text:
            log("hears", "(nothing understood)")
            return self._back_to_listening(gen)
        log("hears", f"“{text}”  ({used})")
        self.face.send({"type": "caption", "text": text})
        self._speak_reply(gen, text=text)

    def _event_reply(self, event: str, detail: str, gen: int) -> None:
        """Something that happened (a timer ended): Arty starts talking by itself, no wake word and no speech to text."""
        log("body", f"telling them about it ({event}{', ' + detail if detail else ''})")
        self._speak_reply(gen, event=event, detail=detail)

    def _speak_reply(self, gen: int, text: str = "", event: str = "", detail: str = "") -> None:
        """Ask the mind and play the answer as it streams in. Shared by spoken turns and events."""
        ask: dict = {"state": self.body_state()}
        if event:
            ask["event"] = event
        if detail:
            ask["detail"] = detail
        started = time.monotonic()
        spoke = False
        try:
            for ev in self.mind.chat(text, **ask):
                if self._stale(gen):
                    return
                kind = ev.get("type")
                if kind == "error":
                    return self._fail(gen, ev.get("message") or "the mind had a problem", chime=EVENT_CHIMES.get(event, ""))
                if kind == "say":
                    if not spoke:
                        log("mind", f"first sentence after {int((time.monotonic() - started) * 1000)} ms")
                        self._mark(gen, "say")
                    spoke = True
                    self._sound.put(("voice", gen, self._voices.submit(self.mind.tts, ev["text"], "wav"), ev["text"]))
                elif kind in ("emote", "look", "sound", "timer", "focus"):
                    if kind == "emote":
                        self.mood = ev.get("name") or self.mood
                    if spoke:
                        self._sound.put(("cue", gen, ev, None))     # in order with the speech
                    else:
                        self._cue(ev)                               # before the first words: at once
        except MindError as e:
            return self._fail(gen, str(e), chime=EVENT_CHIMES.get(event, ""))
        if not spoke:
            if event:
                return self._rest(gen)
            return self._back_to_listening(gen)
        self.turns += 1
        self._sound.put(_Stop(gen))

    def _fail(self, gen: int, why: str, chime: str = "") -> None:
        """The mind or the voice failed: say nothing, show the offline face, chirp sleepy and go back to resting.

        `chime` is played first when the reply was a timer or focus block ending, so that it is still noticed."""
        if self._stale(gen):
            return
        log("body", f"problem: {why}")
        self._go_offline()
        self.face.send({"type": "state", "name": "offline"})
        if chime:
            self._play(chirp(chime))
        self._play(chirp("sleepy"))
        with self._lock:
            self.state = IDLE
        self.wake.reset()

    def _back_to_listening(self, gen: int) -> None:
        if not self._stale(gen):
            self.start_listening(chime=False)

    def _rest(self, gen: int) -> None:
        if not self._stale(gen):
            self._set_state(IDLE)
            self.wake.reset()

    def body_state(self) -> dict:
        return {"mood": self.mood, "lights": True, "local_time": time.strftime("%A %d %B %Y, %H:%M")}

    def _cue(self, ev: dict) -> None:
        kind = ev["type"]
        if kind == "sound":
            self._play(chirp(ev.get("name") or ""))
        elif kind in ("timer", "focus"):
            self._schedule(ev)
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
                    with self._lock:
                        self._gen += 1                  # the rest of this reply is dropped, but not the chirp that follows
                        gen = self._gen
                    self._clear_sound()
                    self._fail(gen, f"no voice: {e}")
                    continue
                if self._stale(gen):
                    continue
                if self.state != SPEAKING:
                    self._set_state(SPEAKING)
                self._log_timing(gen)
                self.speaker.play(wav, on_level=lambda lv: self.face.send({"type": "mouth", "level": round(lv, 3)}))

    def close(self) -> None:
        self.interrupt()
        self._voices.shutdown(wait=False, cancel_futures=True)
