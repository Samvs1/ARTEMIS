"""Start Milo's body: python3 -m body

    python3 -m body                      # microphone, speaker, wake word (or Enter), face on http://127.0.0.1:8001/?face=1
    python3 -m body --push-to-talk       # press Enter instead of saying the wake word
    python3 -m body --fake-audio a.wav b.wav   # feed recordings instead of the microphone; nothing is played
    python3 -m body --list-devices       # show microphones and speakers

The mind server must be running first (python3 mind/server.py).
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from body.config import load_settings          # noqa: E402
from body.loop import Conversation, log         # noqa: E402


class AutoWake:
    """For recordings: no wake word needed, every recording is something said to Milo."""
    available = True

    def feed(self, frame: bytes) -> bool:
        return True

    def reset(self) -> None:
        pass


def paced(frames, convo: Conversation):
    """Recordings arrive faster than real time: hold each frame while Milo is still busy answering."""
    for frame in frames:
        while convo.busy():
            threading.Event().wait(0.02)
        yield frame


class PacedMic:
    def __init__(self, mic, convo_ref: list) -> None:
        self.mic, self.convo_ref = mic, convo_ref

    def frames(self):
        return paced(self.mic.frames(), self.convo_ref[0])


class NoFace:
    def send(self, event: dict) -> None:
        pass

    def stop(self) -> None:
        pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m body", description="Milo's body: hearing, speaking and the face.")
    p.add_argument("--fake-audio", nargs="+", metavar="WAV", help="feed these recordings instead of the microphone")
    p.add_argument("--push-to-talk", action="store_true", help="press Enter instead of saying the wake word")
    p.add_argument("--list-devices", action="store_true", help="list microphones and speakers, then stop")
    p.add_argument("--no-face", action="store_true", help="do not start the face page server")
    args = p.parse_args(argv)

    from body import audio
    if args.list_devices:
        print(audio.list_devices())
        return 0

    settings = load_settings()
    from body.mind_client import MindClient
    from body.stt import build_chain
    from body.wake import PushToTalk, WakeWord

    mind = MindClient(settings.mind_url)
    health = mind.health()
    if not health:
        log("body", f"the mind server does not answer at {settings.mind_url}. Start it first: python3 mind/server.py")
        return 1
    log("body", f"mind: {health.get('brain', {}).get('label', '?')}; voice: {health.get('voice', {}).get('label', '?')}")

    stt = build_chain(settings)
    if args.fake_audio:
        mic, speaker, wake = audio.FakeAudioIn(args.fake_audio, realtime=False), audio.FakeAudioOut(), AutoWake()
    else:
        try:
            mic, speaker = audio.AudioIn(settings.input_device), audio.AudioOut(settings.output_device)
        except Exception as e:                   # no sounddevice, no device: say so in plain words
            log("body", f"no microphone or speaker: {e}")
            return 1
        wake = None
        if settings.wake_model and not args.push_to_talk:
            wake = WakeWord(settings.wake_model, settings.wake_threshold)
            if not wake.available:
                log("body", "the wake word is not available, so press Enter to talk instead")
                wake = None
        wake = wake or PushToTalk()

    if args.no_face:
        face = NoFace()
    else:
        from body.face import FaceServer
        face = FaceServer(settings.face_host, settings.face_port, ROOT / "sim" / "index.html")
        face.start()
        log("face", f"open http://{settings.face_host}:{face.port}/?face=1 for Milo's face")

    ref: list = []
    if args.fake_audio:
        mic = PacedMic(mic, ref)
    convo = Conversation(settings, mic, speaker, wake, stt, mind, face)
    ref.append(convo)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        convo.run(stop)
    finally:
        convo.close()
        face.stop()
        if hasattr(mic, "close"):
            mic.close()
        log("body", f"stopped after {convo.turns} replies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
