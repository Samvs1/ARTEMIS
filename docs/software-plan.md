# Artemis: software plan (R11)

What is left to make Arty work as software, in the order I would do it. Status: **Proposed**. Nothing here is built yet.

## Status (round 14)

| Phase | State |
|---|---|
| 0. Prove the basics | Done. Real DeepSeek run (first words in under a second); housekeeping, CI. The voice half still needs the owner's keys on their computer. |
| 1. The body program | Done (`docs/body-design.md`). Not yet tried with a real microphone and speaker. |
| 2. Memory and people | Done for one person (`docs/memory-design.md`); tried live with DeepSeek. Voice ID left out by decision (row 45). |
| 3. Behaviour | Done (`docs/behaviour-design.md`): timers, focus buddy, good morning and good night, meters kept across a reload, a body that survives without its mind. Tried live with DeepSeek. Waits for a light sensor: good morning and good night on the robot. |
| 4. On the Pi | Waits for List A. |
| 5. The moving body | Waits for List B, stage 2. |

## Where we are

Built and tested against fakes (72 unit tests pass):

- **Mind server** (`mind/`, Python standard library only). DeepSeek for the reply, Fish Audio for the voice, stage directions (`[emote:…]`, `[look:…]`, `[sound:…]`) turned into ordered body events, streamed one line at a time. Personality editor with versions and a "try it" tester. Rate limits, localhost-only.
- **Simulator** (`sim/index.html`). Face with seven emotions, the life layer (blinks, glances, energy, boredom, wish for company), one self-started event ("wants company"), chirps, lip sync from the real voice, typed or browser-microphone input.
- **Memory**: the last 40 messages are saved; the last 12 are sent with each reply.

Not built at all:

- Anything that runs on the robot: listening, wake word, speech to text, playing audio, the link to the motors and sensors.
- Long-term memory, telling people apart, most of the behaviour (welcome home, focus buddy, timers).
- The face running full screen on the Pi and taking orders from another program.

Never tried: real DeepSeek and Fish Audio calls, and the real delay from end of speech to first sound (the target is about one second).

Small problems found while reading:

- The talk budget for self-started chat (5) never refills, so Arty goes quiet for good after five.
- The AI is told about 6 chirps, but the server accepts 10.
- `OPENAI_API_KEY` is in `.env.example` but no code uses it yet. It would be used for speech to text.
- The design log says nicknames last 40 messages; in practice it is 12.

## The plan

The main idea: **most of the robot software can be built and tested on your Windows computer before any hardware arrives**, with a cheap USB microphone and your speakers. The Pi then only adds the screen, the mic array and the motors.

Sizes: S = an afternoon of agent work, M = a day or two, L = several days. "Who" says whether I (the planning session) do it or hand it to a Sonnet agent with a precise brief.

### Phase 0: prove the basics (before anything else)

| # | Work | Size | Who | Needs |
|---|------|------|-----|-------|
| 0.1 | First real run: `--check`, then a real conversation; measure end of speech to first sound | S | you plus me | This cloud session has the DeepSeek key, so the text side can be tested here. Fish Audio (voice) and OpenAI (speech to text) keys are not in the session; the voice half needs your computer or the keys added to the environment |
| 0.2 | Fix whatever the real APIs reject (model names, audio format) | S | me | 0.1 |
| 0.3 | Small fixes: refill the talk budget daily, tell the AI all 10 chirps, correct the nickname note | S | Sonnet | – |
| 0.4 | Project hygiene: `requirements` files per part, a GitHub Actions run of the tests, a start-up hook so cloud sessions can run tests | S | Sonnet | – |

Why first: if the voice round trip is three seconds instead of one, that changes the choices below (for example streaming the voice, or a faster speech-to-text).

### Phase 1: the "body" program (Arty hears and speaks without a browser)

A Python program that does what the browser does today, so the same code later runs on the Pi.

| # | Work | Size | Who | Needs |
|---|------|------|-----|-------|
| 1.1 | Decide the link between the body program, the face page and the mind server (proposal: the body program drives the face over a local WebSocket; the mind server stays as it is) | S | me | – |
| 1.2 | Audio in and out: microphone capture, voice activity detection, playing the voice, the loudness level for the mouth | M | Sonnet | 1.1 |
| 1.3 | Speech to text, cloud first (OpenAI's transcription, the key exists), with a slot for on-device later | M | Sonnet | 1.2, decision A |
| 1.4 | Wake word "Hey Arty" (openWakeWord, which can train a custom word), with push-to-talk as fallback | M | Sonnet | 1.2 |
| 1.5 | Conversation loop: wake, listen, think face, stream the reply, speak sentence by sentence, keep listening a few seconds, stop when you talk over Arty | M | me | 1.2–1.4 |
| 1.6 | Face-only mode of the simulator page (no room, no body drawing, 800 x 480, full screen) that takes orders from the body program | M | Sonnet | 1.1 |
| 1.7 | Chirps as short sound files played by the body program, not the browser | S | Sonnet | 1.2 |

Result: on your computer, say "Hey Arty", talk, interrupt, and see the face react, with no buttons.

### Phase 2: memory and people

| # | Work | Size | Who | Needs |
|---|------|------|-----|-------|
| 2.1 | Timestamps on every saved message, and the time since the last talk in the prompt ("you last spoke yesterday evening") | S | Sonnet | – |
| 2.2 | Long-term memory v1: a plain, readable file of dated facts per person ("things I have learned about you"). After each conversation the AI picks out what is worth keeping; relevant facts go into the prompt | M | me designs, Sonnet builds | decision B |
| 2.3 | Nightly "dreaming": merge the day's facts, drop duplicates, update what changed (the Graphiti idea of "no longer true"), write a short diary entry | M | Sonnet | 2.2 |
| 2.4 | Memory page: read, correct and delete what Arty knows; a "forget everything about me" button | M | Sonnet | 2.2 |
| 2.5 | Telling people apart by voice: enrol each person with a short recording; the body program says who is talking; the prompt and the memory use that person | M–L | me designs, Sonnet builds | Phase 1, decision C |
| 2.6 | Private by default: each person's facts are only used with that person; household facts are shared; a polite guest mode for unknown voices | M | me | 2.2, 2.5 |

### Phase 3: behaviour

| # | Work | Size | Who | Needs |
|---|------|------|-----|-------|
| 3.1 | More self-started events beyond "wants company": good morning, you are back (voice heard after a long quiet), good night; each with a daily budget | M | Sonnet | Phase 1 |
| 3.2 | Perks: timers and focus-buddy mode (works beside you in 25-minute blocks, cheers at breaks) | M | Sonnet | 3.1 |
| 3.3 | Keep the meters and the budget across restarts | S | Sonnet | – |
| 3.4 | Offline behaviour: if the internet drops, Arty still blinks, chirps and looks sleepy or confused instead of dead | S | Sonnet | Phase 1 |
| 3.5 | Personality tuning with real conversations (your part; I help) | ongoing | you plus me | 0.1 |

### Phase 4: on the Pi (when List A arrives)

| # | Work | Size | Who | Needs |
|---|------|------|-----|-------|
| 4.1 | Install script: system packages, the three programs as services that start on boot, the face in kiosk mode, the screen rotated | M | Sonnet | Pi in hand |
| 4.2 | The XVF3800 mic array: capture through its echo cancelling, play through its output, check that you can interrupt Arty | M | me with you | Hardware |
| 4.3 | Real latency and CPU check on the Pi; move parts to the cloud or to the PC if needed | S | me | 4.1, 4.2 |
| 4.4 | Mic-kill and privacy: the face and the mind must know when the microphone is off | S | Sonnet | 4.2 |

### Phase 5: the moving body (when List B stage 2 is bought)

| # | Work | Size | Who | Needs |
|---|------|------|-----|-------|
| 5.1 | Pico 2 firmware: motors with encoders, cliff and front distance sensors, motion sensor; **cliff and bump stops handled on the Pico itself**, so a slow Pi can never drive Arty off a table | L | Sonnet, reviewed by me | Hardware |
| 5.2 | Link protocol between Pi and Pico (USB serial, small messages, a heartbeat that stops the motors if the Pi goes quiet) | M | me | 5.1 |
| 5.3 | `[move:…]` and "turn to who is speaking" (the mic array knows the direction) | M | Sonnet | 5.1, 5.2 |

Later, still parked: camera and faces, ears and glow, dock, look-to-talk.

## Decisions I need from you

- **A. Speech to text:** cloud (OpenAI's transcription; fast, costs a little, audio leaves the house after the wake word) or on the robot (faster-whisper; free and private, probably slower on a Pi). My suggestion: cloud first, keep the slot for on-device.
- **B. Memory:** a plain file of dated facts (my suggestion; readable, easy to correct) or Mem0 straight away.
- **C. Voice ID:** worth doing in the first version, or start with "the owner only" (the design log allows that) and add your partner later?
- **D. Where the mind server lives:** on the Pi, or on your computer with the Pi as a thin client? The plan works either way; on the Pi is simpler for an always-on robot.
- **E. Start Phase 1 now on your computer, before buying hardware?** My suggestion: yes.

## How the work is split

I keep the parts where a wrong choice is expensive: the links between programs (1.1, 5.2), the conversation loop (1.5), the memory design (2.2, 2.6), and review of everything agents write. Sonnet agents get the well-bounded pieces with a written brief, tests to pass, and a rule not to touch other parts. Each piece lands as its own commit with tests, and the design log gets a line per round, as before.
