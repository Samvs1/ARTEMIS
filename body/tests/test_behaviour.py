"""Timers, the focus buddy and life without the mind: the body's side of docs/behaviour-design.md.

Fakes only: a fake clock (nothing sleeps), a mind that plays scripted events (the demo mind never writes timer
or focus tags), a fake speaker, face, transcriber and wake word. The frames are fed by hand, so the tests are
deterministic. One test at the end uses the REAL mind server in mock mode to check the event path end to end.
"""
import contextlib
import io
import os
import tempfile
import threading
import time
import unittest
import wave
from collections import deque
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

try:
    import numpy as np
except ImportError:                     # the audio parts need numpy
    np = None

from body.loop import IDLE, LISTENING, MAX_TIMERS, Conversation, Scheduler, span
from body.mind_client import MindError
from body.tests.test_loop import FakeFace, speech_like_wav

FRAME_BYTES = 960
SILENCE = bytes(FRAME_BYTES)
VOICE = b"RIFF-the-voice-of-the-fake-mind"      # what the fake mind's tts() returns; the fake speaker below does not decode it


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def emote(name="happy"):
    return {"type": "emote", "name": name}


def say(text):
    return {"type": "say", "text": text}


def timer(seconds, label=""):
    return {"type": "timer", "seconds": seconds, "label": label}


CANCEL_TIMERS = {"type": "timer", "cancel": True}
DONE = {"type": "done", "first_token_ms": 1, "total_ms": 2}


def focus(minutes=25):
    return {"type": "focus", "minutes": minutes}


STOP_FOCUS = {"type": "focus", "stop": True}


class ScriptedMind:
    """Stands in for MindClient. Each chat() plays the next script: a list of events, a threading.Event (wait for it)
    or an exception (raise it). Everything asked is written down in `calls`."""

    def __init__(self, scripts=None, healthy=True) -> None:
        self.scripts = deque(scripts or [])
        self.calls: list[dict] = []
        self.spoken: list[str] = []
        self.healthy = healthy
        self.health_calls = 0

    def chat(self, text="", event="", state=None, detail=""):
        self.calls.append({"text": text, "event": event, "detail": detail, "state": state})
        script = self.scripts.popleft() if self.scripts else [emote(), say("Okay."), DONE]
        return self._play(script)

    @staticmethod
    def _play(script):
        for item in script:
            if isinstance(item, threading.Event):
                item.wait(10)
            elif isinstance(item, BaseException):
                raise item
            else:
                yield item

    def tts(self, text, fmt="wav"):
        self.spoken.append(text)
        return VOICE

    def health(self):
        self.health_calls += 1
        return {"ok": True} if self.healthy else None

    def cancel(self):
        pass


class FakeSpeaker:
    """Records what was played; the fake mind's voice is not a real WAV, so this does not decode anything."""

    def __init__(self) -> None:
        self.played: list[bytes] = []

    def play(self, wav, on_level=None):
        self.played.append(bytes(wav))
        if on_level:
            on_level(0.5)
            on_level(0.0)
        return True

    def stop(self):
        pass

    def close(self):
        pass


class FakeWake:
    available = True

    def __init__(self) -> None:
        self._flag = False

    def trigger(self):
        self._flag = True

    def feed(self, frame):
        hit, self._flag = self._flag, False
        return hit

    def reset(self):
        self._flag = False


def speech_frames() -> list[bytes]:
    with wave.open(io.BytesIO(speech_like_wav(1.2)), "rb") as w:
        pcm = w.readframes(w.getnframes())
    return [pcm[i:i + FRAME_BYTES].ljust(FRAME_BYTES, b"\x00") for i in range(0, len(pcm), FRAME_BYTES)]


class Rig:
    """A Conversation with a fake clock, driven by hand: no microphone thread, no sleeping."""

    def __init__(self, test: unittest.TestCase, scripts=None, offline=False, healthy=True, mind=None, speaker=None) -> None:
        from body.chirps import NAMES, chirp
        from body.config import load_settings
        from body.stt import FakeTranscriber, TranscriberChain

        self.clock = FakeClock()
        self.mind = mind or ScriptedMind(scripts, healthy)
        self.speaker, self.face, self.wake = speaker or FakeSpeaker(), FakeFace(), FakeWake()
        self.stt = FakeTranscriber()
        self.chirps = {chirp(name): name for name in NAMES}
        settings = load_settings({"MILO_MIND_URL": "http://127.0.0.1:1"})
        self.convo = Conversation(settings, None, self.speaker, self.wake, TranscriberChain([self.stt]), self.mind,
                                  self.face, clock=self.clock, offline=offline)
        test.addCleanup(self.convo.close)
        self.sentences = 0                                  # how many finished sentences Milo took up to think about
        take_up = self.convo._think

        def counting_think(pcm):
            self.sentences += 1
            take_up(pcm)

        self.convo._think = counting_think
        with contextlib.redirect_stdout(io.StringIO()):
            self.convo.begin()

    # ---- what happened
    def heard(self, since: int = 0) -> list[str]:
        """What the speaker played: 'voice' or the chirp's name."""
        return ["voice" if wav == VOICE else self.chirps.get(wav, "?") for wav in self.speaker.played[since:]]

    def last_state(self) -> str:
        return self.face.states()[-1]

    def until(self, condition, timeout: float = 5.0) -> None:
        """Wait for something the speaker thread or a background check does."""
        end = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > end:
                raise AssertionError(f"waited in vain; played {self.heard()}, faces {self.face.states()}")
            time.sleep(0.005)

    # ---- driving it
    def wake_up(self) -> None:
        """The wake word, if Milo is resting (while it is already listening, the person just talks)."""
        if self.convo.state == IDLE:
            self.wake.trigger()
            self.convo.on_frame(SILENCE)

    def say(self, text: str) -> None:
        """Say something to a listening Milo: a second of quiet, a sentence, then quiet until the sentence is over."""
        self.stt.queue.append(text)
        before = self.sentences
        for frame in [SILENCE] * 33 + speech_frames() + [SILENCE] * 60:
            self.convo.on_frame(frame)
            if self.sentences > before:                     # (the fakes are so quick that Milo may be listening again already)
                return
        raise AssertionError("Milo did not take the sentence")

    def settle(self, timeout: float = 10.0) -> None:
        """Wait until the reply (or event reply) has been spoken to the end."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.convo.busy() and self.convo.state in (LISTENING, IDLE):
                return
            time.sleep(0.005)
        raise AssertionError(f"Milo did not finish: state {self.convo.state}, faces {self.face.states()}")

    def talk(self, text: str) -> None:
        self.wake_up()
        self.say(text)
        self.settle()

    def go_idle(self) -> None:
        """Let the listening window run out (a few seconds of quiet frames). Milo is then resting, unless an
        ended timer was waiting for exactly that and has started its announcement."""
        if self.convo.state != LISTENING:
            return
        mark = len(self.face.events)
        for _ in range(400):
            self.convo.on_frame(SILENCE)
            # (not the state itself: with these quick fakes an announcement may be over, and Milo listening again, already)
            if any(e["type"] == "state" and e["name"] != "listening" for e in self.face.events[mark:]):
                return
        raise AssertionError("Milo never stopped listening")

    def probe(self) -> None:
        """Wait for a background check of the mind, if one is running."""
        thread = self.convo._probe_thread
        if thread is not None:
            thread.join(5)
            if thread.is_alive():
                raise AssertionError("the mind check did not finish")

    def jump(self, seconds: float) -> None:
        """Let time pass and look at the clock once."""
        self.clock.advance(seconds)
        self.convo.tick()


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.scheduler = Scheduler(self.clock)

    def test_a_fourth_timer_replaces_the_oldest(self):
        for label in ("a", "b", "c"):
            self.assertIsNone(self.scheduler.add_timer(60, label)["replaced"])
            self.clock.advance(1)
        self.assertEqual(self.scheduler.add_timer(60, "d")["replaced"], "a")
        self.assertEqual([t["label"] for t in self.scheduler.timers()], ["b", "c", "d"])
        self.assertEqual(MAX_TIMERS, 3)

    def test_ended_things_come_out_once_and_soonest_first(self):
        self.scheduler.add_timer(300, "tea")
        self.scheduler.add_timer(30, "egg")
        self.scheduler.start_focus(5)                           # ends at 300 s as well, but was set last
        self.clock.advance(29)
        self.assertEqual(self.scheduler.due(), [])
        self.clock.advance(2)
        self.assertEqual(self.scheduler.due(), [("timer_done", "egg")])
        self.assertEqual(self.scheduler.due(), [])
        self.clock.advance(1000)
        self.assertEqual(self.scheduler.due(), [("timer_done", "tea"), ("focus_break", "5")])
        self.assertFalse(self.scheduler.focus_active())
        self.assertEqual(self.scheduler.timers(), [])

    def test_cancel_and_stop(self):
        self.scheduler.add_timer(60, "a")
        self.scheduler.add_timer(60, "b")
        self.scheduler.start_focus(25)
        self.assertEqual(self.scheduler.cancel_timers(), 2)
        self.assertTrue(self.scheduler.focus_active())
        self.assertTrue(self.scheduler.stop_focus())
        self.assertFalse(self.scheduler.stop_focus())
        self.clock.advance(10_000)
        self.assertEqual(self.scheduler.due(), [])

    def test_unusable_numbers_are_ignored_and_huge_ones_capped(self):
        for bad in (None, "ten", -5, 0, True, float("nan"), [60]):
            self.assertIsNone(self.scheduler.add_timer(bad, "x"), bad)
            self.assertIsNone(self.scheduler.start_focus(bad), bad)
        self.assertEqual(self.scheduler.timers(), [])
        self.assertEqual(self.scheduler.add_timer(10 ** 9, "forever")["seconds"], 12 * 3600)
        self.assertEqual(self.scheduler.start_focus(10 ** 9), 12 * 60)

    def test_labels_are_tidied(self):
        self.assertEqual(self.scheduler.add_timer(10, "  the   tea \n")["label"], "the tea")
        self.assertEqual(len(self.scheduler.add_timer(10, "x" * 100)["label"]), 40)
        self.assertEqual(self.scheduler.add_timer(10, None)["label"], "")

    def test_restarting_focus_starts_the_block_again(self):
        self.scheduler.start_focus(10)
        self.clock.advance(9 * 60)
        self.scheduler.start_focus(10)
        self.clock.advance(2 * 60)
        self.assertEqual(self.scheduler.due(), [])
        self.clock.advance(9 * 60)
        self.assertEqual(self.scheduler.due(), [("focus_break", "10")])

    def test_span_in_words(self):
        self.assertEqual([span(s) for s in (1, 90, 600, 5400, 3601)],
                         ["1 second", "1 minute 30 seconds", "10 minutes", "1 hour 30 minutes", "1 hour 1 second"])


@unittest.skipIf(np is None, "numpy is not installed")
class TimerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quiet = contextlib.redirect_stdout(io.StringIO())       # the body logs plain-word lines
        cls.quiet.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.quiet.__exit__(None, None, None)

    def test_a_timer_tag_starts_a_timer_and_the_end_is_announced_when_idle(self):
        rig = Rig(self, [
            [emote(), say("Ten minutes for the tea, got it!"), timer(600, "tea"), DONE],
            [emote("excited"), say("Your tea is ready!"), DONE],
        ])
        rig.talk("set a timer for ten minutes for the tea")
        self.assertEqual([(t["label"], round(t["left"])) for t in rig.convo.scheduler.timers()], [("tea", 600)])
        rig.go_idle()
        calls = len(rig.mind.calls)
        before_faces = len(rig.face.events)
        before_sounds = len(rig.speaker.played)

        rig.jump(599)
        self.assertEqual(len(rig.mind.calls), calls, "not yet")
        rig.jump(2)                                                 # the timer has ended and Milo is idle: no wake word needed
        rig.settle()

        asked = rig.mind.calls[-1]
        self.assertEqual((asked["event"], asked["detail"], asked["text"]), ("timer_done", "tea", ""))
        self.assertEqual(len(rig.mind.calls), calls + 1)
        self.assertEqual(len(rig.stt.calls), 1, "no speech to text for an event")
        states = [e["name"] for e in rig.face.events[before_faces:] if e["type"] == "state"]
        self.assertEqual(states[:2], ["thinking", "speaking"])
        self.assertEqual(states[-1], "listening", "the usual listening window opens after the announcement")
        self.assertIn("voice", rig.heard(before_sounds))
        self.assertEqual(rig.mind.spoken[-1], "Your tea is ready!")
        self.assertEqual(rig.convo.scheduler.timers(), [])
        self.assertEqual(rig.convo.turns, 2)

        rig.jump(1000)                                              # once is enough
        self.assertEqual(len(rig.mind.calls), calls + 1)

    def test_a_timer_without_a_label_sends_no_detail(self):
        rig = Rig(self, [[emote(), say("Okay."), timer(30), DONE]])
        rig.talk("timer for thirty seconds")
        rig.go_idle()
        rig.jump(31)
        rig.settle()
        self.assertEqual((rig.mind.calls[-1]["event"], rig.mind.calls[-1]["detail"]), ("timer_done", ""))

    def test_a_timer_before_the_first_words_is_set_at_once(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        rig = Rig(self, [[emote(), timer(120, "pasta"), gate, say("Two minutes for the pasta!"), DONE]])
        rig.wake_up()
        rig.say("timer for the pasta")
        deadline = time.monotonic() + 5
        while not rig.convo.scheduler.timers() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual([t["label"] for t in rig.convo.scheduler.timers()], ["pasta"])
        self.assertEqual(rig.convo.state, "thinking", "no sentence yet, and the timer already runs")
        gate.set()
        rig.settle()

    def test_a_timer_that_ends_during_a_talk_waits_until_milo_is_idle(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        rig = Rig(self, [
            [emote(), say("Thirty seconds."), timer(30, "egg"), DONE],
            [emote(), gate, say("It is about four."), DONE],
            [emote("excited"), say("The egg is done!"), DONE],
        ])
        rig.talk("timer for the egg, thirty seconds")
        rig.wake_up()                                               # a second talk, held up in the mind
        rig.say("what time is it")
        self.assertEqual(rig.convo.state, "thinking")

        rig.jump(31)                                                # the egg is done while Milo is busy
        self.assertEqual(len(rig.mind.calls), 2, "it must wait")
        gate.set()
        rig.settle()
        self.assertEqual(rig.convo.state, LISTENING)
        rig.jump(1)
        self.assertEqual(len(rig.mind.calls), 2, "listening to the person is not idle either")

        rig.go_idle()                                               # the window runs out: now it is idle
        rig.settle()
        self.assertEqual(len(rig.mind.calls), 3)
        self.assertEqual((rig.mind.calls[2]["event"], rig.mind.calls[2]["detail"]), ("timer_done", "egg"))
        self.assertEqual(rig.mind.spoken[-1], "The egg is done!")
        rig.jump(100)
        self.assertEqual(len(rig.mind.calls), 3)

    def test_two_things_ending_together_are_announced_one_after_the_other(self):
        rig = Rig(self, [[emote(), say("Okay."), timer(30, "a"), timer(30, "b"), DONE]])
        rig.talk("two timers")
        rig.go_idle()
        rig.jump(31)
        rig.settle()
        rig.go_idle()
        rig.settle()
        self.assertEqual([(c["event"], c["detail"]) for c in rig.mind.calls[1:]], [("timer_done", "a"), ("timer_done", "b")])

    def test_cancel_stops_every_timer(self):
        rig = Rig(self, [
            [emote(), say("Okay."), timer(30, "a"), timer(60, "b"), DONE],
            [emote(), say("All timers cancelled."), CANCEL_TIMERS, DONE],
        ])
        rig.talk("two timers")
        self.assertEqual(len(rig.convo.scheduler.timers()), 2)
        rig.talk("cancel the timers")
        self.assertEqual(rig.convo.scheduler.timers(), [])
        rig.go_idle()
        rig.jump(10_000)
        rig.settle()
        self.assertEqual(len(rig.mind.calls), 2, "nothing is announced")

    def test_cancel_also_drops_an_ended_timer_that_was_still_waiting(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        rig = Rig(self, [
            [emote(), say("Okay."), timer(30, "a"), DONE],
            [emote(), gate, say("Cancelled."), CANCEL_TIMERS, DONE],
        ])
        rig.talk("a timer")
        rig.wake_up()
        rig.say("never mind the timer")
        rig.jump(31)                                                # ended, but Milo is busy
        gate.set()
        rig.settle()
        rig.go_idle()
        rig.jump(1)
        rig.settle()
        self.assertEqual(len(rig.mind.calls), 2)

    def test_four_timers_in_one_reply_keep_the_newest_three(self):
        rig = Rig(self, [[emote(), say("Four timers."), timer(60, "a"), timer(60, "b"), timer(60, "c"), timer(60, "d"), DONE]])
        rig.talk("four timers please")
        self.assertEqual([t["label"] for t in rig.convo.scheduler.timers()], ["b", "c", "d"])

    def test_a_timer_with_nonsense_is_ignored_and_the_talk_goes_on(self):
        rig = Rig(self, [[emote(), say("Hm."), {"type": "timer", "seconds": "soon", "label": "x"}, {"type": "focus"}, DONE]])
        rig.talk("timer")
        self.assertEqual(rig.convo.scheduler.timers(), [])
        self.assertFalse(rig.convo.scheduler.focus_active())
        self.assertEqual(rig.convo.turns, 1)


@unittest.skipIf(np is None, "numpy is not installed")
class FocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quiet = contextlib.redirect_stdout(io.StringIO())
        cls.quiet.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.quiet.__exit__(None, None, None)

    def test_the_face_rests_as_focus_while_the_block_runs_and_then_the_break_is_announced(self):
        rig = Rig(self, [
            [emote(), say("Twenty-five minutes. I am right here."), focus(25), DONE],
            [emote("excited"), say("Time for a break!"), DONE],
        ])
        self.assertEqual(rig.last_state(), "idle")
        rig.talk("help me focus")
        self.assertTrue(rig.convo.scheduler.focus_active())
        self.assertNotEqual(rig.last_state(), "focus", "still in the talk")
        rig.go_idle()
        self.assertEqual(rig.last_state(), "focus")

        rig.jump(24 * 60)
        self.assertEqual(len(rig.mind.calls), 1)
        self.assertEqual(rig.last_state(), "focus")
        before = len(rig.face.events)
        rig.jump(61)
        rig.settle()
        self.assertEqual((rig.mind.calls[1]["event"], rig.mind.calls[1]["detail"], rig.mind.calls[1]["text"]),
                         ("focus_break", "25", ""))
        states = [e["name"] for e in rig.face.events[before:] if e["type"] == "state"]
        self.assertEqual(states[:2], ["thinking", "speaking"])
        self.assertFalse(rig.convo.scheduler.focus_active())
        rig.go_idle()
        self.assertEqual(rig.last_state(), "idle")
        rig.jump(3 * 3600)
        self.assertEqual(len(rig.mind.calls), 2)

    def test_the_wake_word_works_in_focus_and_the_face_goes_back_to_focus_after_the_talk(self):
        rig = Rig(self, [[emote(), say("Focus it is."), focus(25), DONE], [emote(), say("Sure."), DONE]])
        rig.talk("focus")
        rig.go_idle()
        self.assertEqual(rig.last_state(), "focus")
        before = len(rig.face.events)
        rig.talk("just a question")                                 # talking is fine
        states = [e["name"] for e in rig.face.events[before:] if e["type"] == "state"]
        for earlier, later in (("listening", "thinking"), ("thinking", "speaking")):
            self.assertIn(later, states[states.index(earlier) + 1:], states)
        rig.go_idle()
        self.assertEqual(rig.last_state(), "focus")
        self.assertTrue(rig.convo.scheduler.focus_active())

    def test_focus_stop_ends_the_block_without_an_event(self):
        rig = Rig(self, [
            [emote(), say("Focus it is."), focus(25), DONE],
            [emote(), say("Okay, we are done."), STOP_FOCUS, DONE],
        ])
        rig.talk("focus")
        rig.go_idle()
        self.assertEqual(rig.last_state(), "focus")
        rig.talk("stop focusing")
        self.assertFalse(rig.convo.scheduler.focus_active())
        rig.go_idle()
        self.assertEqual(rig.last_state(), "idle")
        rig.jump(2 * 3600)
        rig.settle()
        self.assertEqual(len(rig.mind.calls), 2, "no focus_break after a stop")

    def test_a_block_that_ends_during_a_talk_waits_until_milo_is_idle(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        rig = Rig(self, [
            [emote(), say("Okay."), focus(5), DONE],
            [emote(), gate, say("Hello."), DONE],
            [emote("excited"), say("Break time!"), DONE],
        ])
        rig.talk("focus for five")
        rig.wake_up()
        rig.say("hi")
        rig.jump(5 * 60 + 1)
        self.assertEqual(len(rig.mind.calls), 2)
        gate.set()
        rig.settle()
        rig.go_idle()
        rig.settle()
        self.assertEqual((rig.mind.calls[2]["event"], rig.mind.calls[2]["detail"]), ("focus_break", "5"))


@unittest.skipIf(np is None, "numpy is not installed")
class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.out = io.StringIO()
        quiet = contextlib.redirect_stdout(self.out)
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def lines(self):
        return [line for line in self.out.getvalue().splitlines() if line.strip()]

    def test_starting_without_the_mind_shows_offline_and_the_wake_word_gets_a_sleepy_chirp(self):
        rig = Rig(self, offline=True, healthy=False)
        self.assertEqual(rig.face.states(), ["offline"])
        rig.wake_up()
        rig.until(lambda: len(rig.speaker.played) >= 2)
        self.assertEqual(rig.heard(), ["listen", "sleepy"])
        self.assertEqual(rig.convo.state, IDLE, "no listening and no thinking forever")
        self.assertEqual(rig.last_state(), "offline")
        self.assertNotIn("thinking", rig.face.states())
        self.assertEqual(rig.mind.calls, [])
        self.assertEqual(rig.stt.calls, [])

    def test_a_wake_word_that_repeats_at_once_gets_one_chirp(self):
        rig = Rig(self, offline=True, healthy=False)
        rig.wake_up()
        rig.wake_up()
        rig.clock.advance(3)
        rig.wake_up()
        rig.until(lambda: len(rig.speaker.played) >= 4)
        self.assertEqual(rig.heard(), ["listen", "sleepy", "listen", "sleepy"])

    def test_the_mind_is_asked_every_ten_seconds_and_the_face_goes_back_to_idle_when_it_answers(self):
        rig = Rig(self, offline=True, healthy=False)
        self.out.truncate(0)
        self.out.seek(0)
        rig.jump(9)
        rig.probe()
        self.assertEqual(rig.mind.health_calls, 0)
        for _ in range(5):                                          # five failed checks, 11 s apart
            rig.jump(11)
            rig.probe()
        self.assertEqual(rig.mind.health_calls, 5)
        self.assertEqual(rig.last_state(), "offline")
        self.assertEqual(self.lines(), [], "a failed check is not worth a log line")

        rig.mind.healthy = True
        rig.jump(5)
        rig.probe()
        self.assertEqual(rig.mind.health_calls, 5, "not due yet")
        rig.jump(6)
        rig.probe()
        self.assertEqual(rig.mind.health_calls, 6)
        self.assertFalse(rig.convo.offline)
        self.assertEqual(rig.last_state(), "idle")
        self.assertEqual(len(self.lines()), 1, self.lines())
        rig.jump(100)                                               # online: no more checks
        rig.probe()
        self.assertEqual(rig.mind.health_calls, 6)

        rig.talk("hello again")                                     # and a normal talk works
        self.assertEqual(rig.mind.calls[-1]["text"], "hello again")
        self.assertIn("speaking", rig.face.states())

    def test_the_wake_word_while_offline_asks_the_mind_at_once(self):
        rig = Rig(self, offline=True, healthy=True)                 # it is back, but the next check is 10 s away
        rig.wake_up()
        rig.probe()
        self.assertEqual(rig.mind.health_calls, 1)
        self.assertEqual(rig.last_state(), "idle")
        rig.until(lambda: len(rig.speaker.played) >= 2)
        self.assertEqual(rig.heard(), ["listen", "sleepy"])

    def test_a_mind_that_drops_during_a_talk_means_offline_and_it_comes_back_by_itself(self):
        rig = Rig(self, [[MindError("Could not reach Milo's mind at http://x (refused). Is the mind server running?")]], healthy=False)
        rig.wake_up()
        rig.say("hello")
        rig.settle()
        rig.until(lambda: len(rig.speaker.played) >= 3)
        self.assertEqual(rig.last_state(), "offline")
        self.assertEqual(rig.heard(), ["listen", "thinking", "sleepy"])
        self.assertTrue(rig.convo.offline)
        self.assertEqual(rig.convo.turns, 0)
        rig.jump(11)
        rig.probe()
        self.assertEqual(rig.last_state(), "offline")
        rig.mind.healthy = True
        rig.jump(11)
        rig.probe()
        self.assertEqual(rig.last_state(), "idle")

    def test_a_voice_that_fails_means_offline_and_a_sleepy_chirp(self):
        rig = Rig(self, [[emote(), say("Hello there."), DONE]], healthy=False)

        def no_voice(text, fmt="wav"):
            raise MindError("The voice service is not answering.")

        rig.mind.tts = no_voice
        rig.talk("hi")
        rig.until(lambda: rig.heard()[-1:] == ["sleepy"])
        self.assertEqual(rig.last_state(), "offline")
        self.assertNotIn("voice", rig.heard())
        self.assertEqual(rig.convo.turns, 1)                         # (the reply itself was fine)
        self.assertTrue(rig.convo.offline)

    def test_a_timer_that_ends_while_the_mind_is_away_still_rings(self):
        rig = Rig(self, [[emote(), say("Okay."), timer(30, "tea"), DONE], [MindError("no mind")]], healthy=False)
        rig.talk("timer")
        rig.go_idle()
        rig.jump(31)                                                # the mind fails on the event ...
        rig.settle()
        rig.until(lambda: rig.heard()[-2:] == ["excited", "sleepy"])        # a chime first, so the end is still noticed
        self.assertEqual(rig.last_state(), "offline")
        self.assertTrue(rig.convo.offline)

        rig.convo.scheduler.add_timer(5, "again")                   # ... and now that it is known to be away, it does not even ask
        calls = len(rig.mind.calls)
        before = len(rig.speaker.played)
        rig.jump(6)
        rig.until(lambda: len(rig.speaker.played) > before)
        self.assertEqual(rig.heard(before), ["excited"])
        self.assertEqual(len(rig.mind.calls), calls)

    def test_main_starts_without_a_mind_instead_of_quitting(self):
        from body.__main__ import main

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "hello.wav"
            wav.write_bytes(speech_like_wav())
            with mock.patch.dict(os.environ, {"MILO_MIND_URL": "http://127.0.0.1:1"}):
                code = main(["--fake-audio", str(wav), "--no-face"])
        self.assertEqual(code, 0)
        said = self.out.getvalue()
        self.assertIn("does not answer", said)
        self.assertIn("without it", said)
        self.assertEqual(said.count("does not answer"), 1)


@unittest.skipIf(np is None, "numpy is not installed")
class RealMindTests(unittest.TestCase):
    """The event path against the REAL mind server in mock mode (the demo mind answers any event)."""

    @classmethod
    def setUpClass(cls):
        from mind.config import Settings
        from mind.server import App, Handler

        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.app = App(Settings(mock=True), data_dir=Path(cls.tmp.name))
        cls.server.daemon_threads = True
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.quiet = contextlib.redirect_stdout(io.StringIO())
        cls.quiet.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.quiet.__exit__(None, None, None)
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_a_timer_event_gets_a_real_reply_that_is_spoken(self):
        from body.audio import FakeAudioOut
        from body.mind_client import MindClient

        rig = Rig(self, mind=MindClient(self.url), speaker=FakeAudioOut())
        rig.convo.scheduler.add_timer(30, "tea")
        rig.go_idle()
        rig.jump(31)
        rig.settle(timeout=30)
        states = rig.face.states()
        self.assertEqual(states[-3:], ["thinking", "speaking", "listening"], states)
        self.assertTrue(rig.speaker.played, "the voice was played")
        self.assertEqual(rig.convo.turns, 1)


if __name__ == "__main__":
    unittest.main()
