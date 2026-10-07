# Milo: Design Log

A living record of the brainstorm for Milo, a small AI companion robot that lives in the apartment. Decisions are logged as they are made; ideas and open questions stay visible until they are resolved.

Status tags:

- **Decided**: chosen by the owner.
- **Assumed**: a working assumption, not yet confirmed.
- **Proposed**: a suggestion on the table, not yet discussed or chosen.

![Milo concept v0](img/milo-concept-v0.webp)

*Concept v0 (generated render). Treat it as a mood board, not an engineering drawing: several details, such as the wheel layout, are still open.*

## 1. Vision

Milo is a small wheeled robot that hangs out in the apartment. A remote AI is its mind; the body gives it a face, a voice and a physical presence. You can talk to it, it has a personality, it remembers you, and the relationship deepens over time.

> Your little AI companion at home. Curious, friendly, a little mischievous.

From the concept: talks and listens, sees and understands, remembers, moves around, shows emotions, grows with you. Concept emotions: Happy, Curious, Surprised, Thinking, Excited, Sleepy.

Reference points mentioned so far: Anki Vector and Cozmo, EMO, Jibo (stationary social robot), Stack-chan (hobbyist desk robot).

## 2. Decisions so far

| # | Topic | Decision | Notes | Status |
|---|-------|----------|-------|--------|
| 1 | Initiative | Alive but polite | The local life layer is always on and free: blinking, glancing, perking up when you walk in, dozing off at night. Milo only starts a conversation at meaningful moments (you got home, it has been quiet for hours), within a daily talking budget. | Decided (R1) |
| 2 | Territory | Floor roamer, table-safe | Follows you room to room. Big wheels, bump and cliff sensing (balcony sills, table edges), a dock, and a neck that tilts up to see faces. | Decided (R1) |
| 3 | Character | Strong fixed character that grows closer | The core personality stays consistent; the relationship (memories, inside jokes, habits) is what evolves. | Decided (R1) |
| 4 | Voice | Natural, expressive voice plus local chirps | Chirps and hums play on the robot for instant reactions (no latency, no LLM cost). | Decided (R1) |
| 5 | Brain and privacy | Cloud AI with hard privacy switches | Audio streams only after the wake word or while a conversation is live. Physical mic-kill switch; camera shutter (head tips down) with an LED to confirm. | Decided (R2) |
| 6 | Language | English only | Widest choice of low-latency voices and wake-word models. Other languages are a later maybe. | Decided (R2) |
| 7 | Household | Mainly the owner, with a partner or roommate sharing the space | The answer was ambiguous ("just me" plus "partner / roommates"). Design for two people from day one: separate memories, and a friendly but different stance toward the second person. | Assumed (R2), to confirm |
| 8 | Signature moments | All four: welcome home, company while you work, evening chats, silly play | To be staged; see the roadmap proposal. | Decided (R2) |
| 9 | Usefulness | Companion plus a few perks | Timers, a focus-buddy mode, music, maybe smart-home lights. Character first; no calendar, email or open-ended web agent for now. | Decided (R3) |
| 10 | Stability | Rear skid, balance-ready | Stable at rest and when unpowered. Place weight and wheels so a self-balancing mode can be added later. | Decided (R3) |
| 11 | Extra body language | Ears or antennae (two servos) and a mood glow (LED) | Arms or a lift are deferred (v2 at the earliest). | Decided (R3) |
| 12 | Design log | Keep it in the repo | Committed to the working branch after each round. | Decided (R3) |
| 13 | Name and wake word | Milo, wake word "Hey Milo" | Working name; confirm before a wake-word model is trained. | Assumed |
| 14 | Size | About 190 x 160 x 135 mm, as in the concept | Tight for a Pi 5, a 3.5" screen and a battery; verify with a CAD mock-up before committing. | Assumed |

## 3. Architecture principle: two brains

Because the AI is remote, there is a gap of roughly a second between you finishing a sentence and Milo's answer. The work is split so Milo never feels dead during that gap.

**Life layer (on the robot: instant, works offline)**

- Idle animation: blinking, glances, small body motions, drowsiness at night.
- Reflexes: turn toward a voice, follow faces, stop at cliffs and bumps, react to being lifted or petted.
- Emotion state: the six concept emotions plus slow meters (boredom, curiosity, social need, energy).
- Sounds: the chirp and hum palette.
- Initiative engine: decides when something is worth waking the remote mind. This is how "polite" is implemented.
- Wake word, voice activity detection, and the privacy hardware (mic-kill, camera shutter logic, LEDs).

**Mind (remote)**

- Conversation, personality (the character bible), memory and reasoning.
- Directs the body through a small set of tools instead of driving motors. Placeholder names: `emote`, `look_at`, `move`, `play_sound`, `remember`.
- Receives context with each turn: current emotion and meters, time of day, who is present, recent events.

Latency hiding: the **Thinking** face and a quick "hmm" sound start the moment you stop speaking. The target from end of speech to first sound is about one second (to be validated early).

Failure mode: if the network or the remote mind is unreachable, Milo stays alive on the life layer and looks visibly sleepy or confused instead of dead.

## 4. Character

Core: curious, friendly, a little mischievous (Decided).

Proposed seeds, not yet chosen:

- **Character bible** with wants, fears and quirks, written together with the owner. Examples: wants to find out what is behind every closed door; afraid of the vacuum cleaner and the dark; always parks in the sunbeam. These drive both the remote mind's lines and the local behaviors.
- **Growing closer** through memory: inside jokes, remembered preferences, a shared history.
- **Dreaming at the dock**: while charging at night, Milo replays the day and boils it down to a few "things I learned about you", which feeds the memory system. In the morning it may say "I dreamt about what you said yesterday".
- **Chirp language**: a palette of chirps and hums keyed to emotional states, so you can read Milo's mood by sound alone.

## 5. Behavior and signature moments

All four moments are wanted. Ideas so far:

| Moment | Ideas | Leans on |
|--------|-------|----------|
| Welcome home | "Waiting at the door": Milo learns roughly when you get home and drifts toward the entrance a few minutes early. Your phone joining the Wi-Fi could be an early signal. A different greeting each time. | Life layer, light remote use |
| Company while you work | Focus buddy: works beside you in a Pomodoro rhythm, dozes during deep focus, victory spin at breaks. Reacts quietly and rarely speaks. | Life layer, almost no LLM |
| Evening chats | Winds down with you, talks about the day, brings up things you said last week. | Remote mind, memory |
| Silly play | Games, chasing, dancing to music, hide-and-seek. | Drive base, perception |

## 6. Body

### From the concept render

- Raspberry Pi 5 (4 GB or more) as the main computer.
- Two motors with encoders.
- 3.5" touch display (the face), camera and microphone array in the head, neck tilt.
- Speaker and amplifier; Wi-Fi and Bluetooth.
- Rechargeable battery (the concept claims about 2 to 6 hours); USB-C charging port at the back.
- 3D printed, screw-together modular body for standard FDM printers.
- Four interchangeable shells on the same hardware: Classic, Minimal, Retro, Creature.
- Concept size: about 190 mm tall, 160 mm wide, 135 mm deep.

### Decided changes to the render

- Floor roamer with bump and cliff sensing; rear skid for stability, balance-ready.
- Ears or antennae (two servos) and a mood glow (LED).

### Proposed, not yet discussed

- **Split brain in hardware**: a microcontroller handles real-time work (motors, IMU, cliff and bump sensors, servos, LEDs) next to the Pi, which handles networking, audio, display and camera.
- **Voice front end**: a multi-microphone array with echo cancellation and direction-of-arrival, so Milo can hear over its own speaker and motors, and turn toward whoever is speaking.
- **Display**: a panel with a smooth refresh rate (DSI or HDMI class) rather than a slow SPI panel, so the eyes animate well. Touch is optional.
- **Pan by driving**: the wheels pivot the whole body to look left or right, so the neck only needs tilt.
- **Sensors**: IMU (picked up, tilted, bumped), downward cliff sensors, a front distance sensor, an ambient light sensor, and optional capacitive touch on the head for petting.
- **Power and thermals**: the Pi 5 is power-hungry and sensitive to voltage sag, and a closed 3D-printed shell traps heat. Plan regulation and cooling early.
- **Dock**: a contact-charging dock with a ramp and a visual marker Milo can find. Plain USB-C charging is acceptable for early prototypes.
- **Shells**: since the hardware is shared, personas could map to shells later.

## 7. Privacy and safety

Decided:

- Audio streams only after the wake word or while a conversation is live.
- Physical mic-kill switch. Camera shutter by tipping the head down, confirmed by an LED.

To work out:

- What is stored remotely, for how long, and how to delete it.
- Identity and memory for two people: what is private to whom.
- A polite "stranger" mode for guests.
- Motor torque and speed limits, and no pinch points at the neck, ears and wheels.

## 8. Open questions

- **Voice and brain stack**: speech-to-text, text-to-speech and LLM choices; realtime framework and transport; model routing (a fast model for small talk, a stronger one for depth); monthly cost budget.
- **Memory design**: what is stored, how it is summarized, how people are recognized (face and voice).
- **Household**: how often is the partner or roommate around, and is Milo theirs too?
- **Name and wake word**: confirm "Milo" and "Hey Milo".
- **Body**: battery and power, display type, mic array, camera, sensors, dock design, size feasibility.
- **Builder context**: 3D printer access, electronics experience, budget, timeline.
- **First prototype**: what should it prove?
- **Character bible**: draft it together.

## 9. Roadmap (proposal, not agreed)

Start with the riskiest and most magical part, which is talking.

1. **Voice loop on a desk rig** (no wheels): Pi, mic, speaker and screen running wake word, speech-to-text, LLM and text-to-speech with streaming. Validate about one second of latency and find Milo's voice.
2. **Life layer**: idle animation, emotion state, chirps, turn toward voice, privacy hardware.
3. **Body v1**: chassis with drive, skid, bump and cliff sensors, neck tilt, ears and glow.
4. **Memory and growth**: long-term memory, two-person identity, nightly consolidation.
5. **Dock and moments**: dock, welcome home, focus buddy, play.

## 10. Round log

**Round 1: what Milo is**

- Initiative: alive but polite. Territory: floor roamer, table-safe. Character: strong character that grows closer. Voice: natural voice plus chirps.

**Round 2: brain, language, household, moments**

- Privacy: cloud is fine with hard privacy switches. Language: English only. Household: "just me" and "partner / roommates" (ambiguous). Moments: all four.

**Round 3: body and scope**

- Usefulness: companion plus a few perks. Stability: rear skid, balance-ready. Expression: ears or antennae, and mood glow. Design log: yes, committed after each round.
