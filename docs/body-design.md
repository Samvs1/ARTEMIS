# Milo: body program design (R12)

The "body" is the Python program that lets Milo hear and speak without a browser. It runs on the Pi next to the mind server, and it can run on a Windows or Linux computer during development. Status: **Decided (R12)**, built in steps; timers, the focus state and running without the mind were added in R14 (`docs/behaviour-design.md`).

Owner decisions this design follows (R12): cloud speech to text with a local fallback; plain-file memory; the owner only at first (memory laid out per person, no voice ID yet); the mind server runs on the Pi; start before the hardware arrives.

## Three programs

```
 microphone ──► body (port 8001) ──HTTP──► mind server (port 8000) ──► DeepSeek, Fish Audio
 speaker   ◄──      │   ▲
                    │   └─ /input (taps on the screen, later)
                    └─ /events (Server-Sent Events) ──► face page in Chromium kiosk (sim/index.html?face=1)
```

- **Mind server** (`mind/`, exists): conversation, personality, memory, voice. Unchanged except that `/api/tts` accepts `"format": "wav"`.
- **Body** (`body/`, new): microphone, wake word, voice activity, speech to text, playing the voice and chirps, the conversation loop, and the face link. Later: motors, sensors, initiative.
- **Face page** (`sim/index.html?face=1`, served by the body): draws only the face, full screen, with its own blinks and glances. It does what the body tells it over `/events`. It never calls the mind, never listens, and never makes sound.

Why the body drives the face (and not the other way round): all audio has to leave through the mic array so its echo cancelling works, the motors will be Python anyway, and a crashed browser must not stop Milo from hearing.

## Rules for all body code

- Python 3.11 (Raspberry Pi OS Bookworm) and newer. Standard library plus `numpy` and `sounddevice`. Everything else (`webrtcvad`, `openwakeword`, `faster-whisper`) is optional: import it inside a function, and fall back cleanly with a log line when missing.
- Every part that touches hardware or the network has a fake, so the tests run in a cloud container with no microphone, no speaker and no keys. Tests use `unittest`, live in `body/tests/`, and run with `python3 -m unittest discover -s body/tests -t .`. Tests that need `numpy` skip themselves when it is missing.
- Audio inside the body is always **16 kHz, mono, int16**, in **frames of 480 samples (30 ms)** as `bytes`. Playback takes 16-bit mono WAV bytes at any rate.
- Keys and settings come from the environment and the project's `.env` (reuse `mind.config.load_env_file`). Never log a key.
- Plain-word log lines, like the mind server: `log("hears", "...")`.

## Modules and their interfaces

These signatures are the contract between pieces built by different people. Keep them.

### `body/config.py`

```python
@dataclass(frozen=True)
class BodySettings:
    mind_url: str = "http://127.0.0.1:8000"      # MILO_MIND_URL
    face_host: str = "127.0.0.1"                  # MILO_FACE_HOST
    face_port: int = 8001                         # MILO_FACE_PORT
    stt_order: tuple[str, ...] = ("openai", "local")   # MILO_STT_ORDER, comma separated
    openai_api_key: str = ""                      # OPENAI_API_KEY
    openai_stt_model: str = "gpt-4o-mini-transcribe"   # MILO_OPENAI_STT_MODEL
    local_stt_model: str = "tiny.en"              # MILO_LOCAL_STT_MODEL (faster-whisper size)
    wake_model: str = "hey_jarvis"                # MILO_WAKE_MODEL: openWakeWord name or .onnx path; "" = push to talk
    wake_threshold: float = 0.5                   # MILO_WAKE_THRESHOLD
    input_device: str = ""                        # MILO_INPUT_DEVICE: part of a device name, "" = default
    output_device: str = ""                       # MILO_OUTPUT_DEVICE
    barge_in: bool = False                        # MILO_BARGE_IN: talk over Milo to interrupt (needs echo cancelling)
    window_seconds: float = 6.0                   # MILO_WINDOW_SECONDS: keep listening after Milo speaks

def load_settings(env: dict | None = None) -> BodySettings: ...
```

`hey_jarvis` is a placeholder: openWakeWord ships it ready-made. A custom "Hey Milo" model is trained later and set with `MILO_WAKE_MODEL`.

### `body/audio.py`

```python
FRAME = 480; RATE = 16000

class AudioIn:                  # sounddevice microphone
    def __init__(self, device: str = ""): ...
    def frames(self) -> Iterator[bytes]: ...       # endless 30 ms frames; blocks
    def close(self) -> None: ...

class FakeAudioIn:              # for tests and --fake-audio
    def __init__(self, wav_paths_or_bytes: list, gap_seconds: float = 1.0, realtime: bool = False): ...
    def frames(self) -> Iterator[bytes]: ...       # the files resampled to 16 kHz mono, silence between them, then ends

class AudioOut:                 # sounddevice speaker
    def __init__(self, device: str = ""): ...
    def play(self, wav: bytes, on_level: Callable[[float], None] | None = None) -> bool: ...
        # blocks until done or stopped; calls on_level(0..1) about 30 times a second; True if played to the end
    def stop(self) -> None: ...                    # safe from any thread
    def close(self) -> None: ...

class FakeAudioOut:             # records what was played, plays instantly (or in real time)
    played: list[bytes]

def list_devices() -> str: ...                     # for --list-devices
def wav_info(wav: bytes) -> tuple[int, int, int]   # (rate, channels, n_samples)
```

### `body/vad.py`

```python
class VoiceDetector:            # webrtcvad if installed, else an energy threshold that adapts to room noise
    def is_speech(self, frame: bytes) -> bool: ...

class Utterances:               # turns frames into finished utterances
    def __init__(self, vad: VoiceDetector, start_frames: int = 6, end_silence_ms: int = 700,
                 max_seconds: float = 15.0, no_speech_timeout: float | None = None): ...
    def feed(self, frame: bytes) -> bytes | None: ...   # returns a whole utterance (16 kHz mono int16 PCM) when it ends
    def speaking(self) -> bool: ...                      # someone is talking right now
    def timed_out(self) -> bool: ...                     # no speech within no_speech_timeout since reset()
    def reset(self, no_speech_timeout: float | None = None) -> None: ...

def pcm_to_wav(pcm: bytes, rate: int = 16000) -> bytes: ...
```

### `body/wake.py`

```python
class WakeWord:                 # openWakeWord when installed and wake_model is set
    def __init__(self, model: str, threshold: float = 0.5): ...
    available: bool              # False when openwakeword or the model is missing
    def feed(self, frame: bytes) -> bool: ...      # True once when the wake word is heard
    def reset(self) -> None: ...

class PushToTalk:               # fallback: Enter on the keyboard (a thread reading stdin) or trigger() from code
    def feed(self, frame: bytes) -> bool: ...
    def trigger(self) -> None: ...
```

### `body/chirps.py`

The 10 chirps the server accepts (`happy curious surprised thinking excited sleepy wake listen bored poke`), synthesised with numpy in the "soft, organic, musical" style of the simulator (port the shapes from `CHIRPS` in `sim/index.html`). Rendered once and cached.

```python
NAMES: tuple[str, ...]
def chirp(name: str) -> bytes: ...      # 16-bit mono WAV, 0.1 to 0.8 s, quiet; unknown names give a soft default
```

### `body/stt.py`

```python
class Transcriber(Protocol):
    name: str
    def available(self) -> bool: ...
    def transcribe(self, wav: bytes) -> str: ...   # raises SttError with a plain-word message

class OpenAITranscriber:        # POST https://api.openai.com/v1/audio/transcriptions, multipart, stdlib urllib,
    ...                          # language "en", timeout 15 s, model from settings
class LocalWhisper:             # faster-whisper, model loaded once on first use, CPU int8
    ...
class FakeTranscriber:          # returns queued texts
    ...
class TranscriberChain:         # tries each available one in order; returns (text, used_name)
    def __init__(self, items: list[Transcriber]): ...
    def transcribe(self, wav: bytes) -> tuple[str, str]: ...
def build_chain(settings) -> TranscriberChain: ...
```

### `body/mind_client.py`

```python
class MindClient:
    def __init__(self, base_url: str): ...
    def health(self) -> dict | None: ...
    def chat(self, text: str = "", event: str = "", state: dict | None = None, detail: str = "") -> Iterator[dict]: ...
        # streams the server's NDJSON events: emote, look, sound, timer, focus, say, error, done; sends the Origin header the server expects.
        # `event` (+ `detail`, sent only when given) asks for a reply to something that happened, e.g. event "timer_done", detail "tea"
    def tts(self, text: str, fmt: str = "wav") -> bytes: ...   # raises MindError
    def cancel(self) -> None: ...                  # closes the open chat stream (the server sees an interrupt)
```

### `body/face.py`

```python
class FaceServer:               # stdlib ThreadingHTTPServer on face_host:face_port
    def __init__(self, host: str, port: int, sim_file: Path): ...
    def start(self) -> None: ...                   # background thread
    def send(self, event: dict) -> None: ...       # broadcast to every connected page
    def stop(self) -> None: ...
    inputs: queue.Queue                            # events posted by the page to /input
```

Routes: `GET /` serves `sim/index.html`; `GET /events` is a Server-Sent Events stream (one `data: {json}` per event, a comment line every 15 s as a keep-alive); `POST /input` takes `{"type": "touch", "where": "face"}` from the page. Same local-only host rule as the mind server.

Events the body sends to the face:

| Event | Meaning |
|---|---|
| `{"type": "state", "name": "idle" \| "listening" \| "thinking" \| "speaking" \| "focus" \| "sleeping" \| "offline" \| "privacy"}` | What Milo is doing; the face shows it (listening: attentive eyes; thinking: the thinking face; focus: calm, half-lidded, steady eyes, shown instead of `idle` while a focus block runs; offline: sleepy and confused) |
| `{"type": "emote", "name": ...}` and `{"type": "look", "dir": ...}` | Straight from the mind's stage directions |
| `{"type": "mouth", "level": 0.0..1.0}` | About 30 per second while speaking |
| `{"type": "caption", "text": ...}` | Optional, for debugging; hidden unless `&captions=1` |

The mind's `timer` and `focus` stage directions are not sent to the face: the loop keeps the timers itself, and a focus block only shows as the `focus` state. A page that does not know a state name ignores it (`focus` needs the face page's focus look, see `docs/behaviour-design.md`).

The face page in `?face=1` mode: no room, body, buttons or panels; the face fills the window (designed for 800 x 480); the life layer keeps blinking, glancing and breathing but makes no sound, starts nothing and never calls the mind; it reconnects to `/events` every few seconds and looks sleepy while disconnected.

### `body/loop.py` (the conversation)

```
idle ──wake word──► listening ──utterance──► thinking ──first "say"──► speaking ──done──► listening (window) ──silence──► idle
                        ▲                                                  │
                        └────────────── you talk over Milo (barge-in) ─────┘
idle ──a timer or focus block ends (no wake word, no speech to text)──► thinking ──► speaking ──► listening (window) ──► idle
```

- On wake: chirp `listen`, face `listening`, start an utterance with a 6 s no-speech timeout.
- On an utterance: face `thinking`, chirp `thinking`, transcribe, send to the mind with the body state (mood, lights, local time).
- While the reply streams: apply emote and look at once until the first sentence, then in order with the speech. Ask for each sentence's voice as soon as it exists, play them in order, and feed the mouth. `timer` and `focus` events are applied the same way (at once before the first sentence, afterwards in order with the speech).
- After the reply: listen again for `window_seconds` without the wake word, then go back to resting.
- Barge-in (only when `barge_in` is on): speech during playback stops the voice, cancels the reply and starts listening.
- If the mind or the voice fails: say nothing, face `offline`, chirp `sleepy`, log the reason, go back to resting (now in offline mode, see below).
- Ctrl+C stops cleanly.

**The resting face** is `idle`, or `focus` while a focus block runs, or `offline` while the mind is away (offline wins). After every talk the face returns to the resting face. During a focus block the wake word still works (talking is fine); no talk starts by itself except the end of a timer.

**Timers and the focus block** (R14, `docs/behaviour-design.md`). The `Scheduler` in `body/loop.py` keeps up to 3 timers (a fourth replaces the oldest; `{"type": "timer", "cancel": true}` clears them all, also those that ended but were not announced yet) and one focus block (`{"type": "focus", "minutes": n}` starts or restarts it, `{"type": "focus", "stop": true}` ends it without an event). It only looks at the `clock` given to `Conversation` (default `time.monotonic`), so tests use a fake clock and never sleep. Every microphone frame calls `Conversation.tick()`, which asks the scheduler what has ended:

- Milo is idle (resting, nothing queued for the speaker): start a reply at once, with no wake word and no speech to text: `mind.chat(event="timer_done", detail=label)` or `mind.chat(event="focus_break", detail=str(minutes))`. It goes through the same streaming and speaking path as a normal reply (face `thinking`, then `speaking`), and afterwards the usual listening window opens.
- Milo is busy (thinking, speaking, or listening during a talk): the event waits in a queue and starts on the first tick after Milo is idle again. Several ended things are announced one after the other.
- The mind is away: the reply cannot be written, so the body plays a chime instead (chirp `excited` for a timer, `happy` for a focus break). If the mind fails during the announcement itself, that chime is played before the usual sleepy chirp.

**Offline** (R14). `Conversation(..., offline=True)` starts in offline mode, and a failed reply switches to it:

- The face shows `offline`. The wake word still works: chirp `listen`, then chirp `sleepy`, face stays `offline`; no listening, no speech to text, no mind call (a repeated wake word within 2 s is ignored).
- A background check, `mind.health()` in a small thread, runs every 10 s (and once at once after a wake word); the mic loop is never blocked. When the mind answers, the body leaves offline mode and the face goes back to the resting face.
- The log tells about the change once (going offline, coming back), not about every check.

### `body/__main__.py`

`python3 -m body` with flags `--fake-audio FILE [FILE ...]` (feed WAV files instead of the microphone, and print instead of playing), `--push-to-talk` (Enter instead of the wake word), `--list-devices`, `--no-face`.

If the mind server does not answer at start, the body does not stop: it logs one plain-word line and starts in offline mode (above).

## Changes outside `body/`

- `mind/voice.py`, `mind/server.py`: `/api/tts` takes an optional `"format"` (`"mp3"` default, `"wav"`); Fish Audio is asked for that format; the demo voice is already WAV.
- `sim/index.html`: the `?face=1` mode above; plus the daily refill of the self-started talk budget (5 per day, kept in `localStorage`).

## Not in this step

Memory (next), voice ID, motors, initiative in the body, training the "Hey Milo" wake word, streaming voice.
