# Artemis: behaviour design (R14)

Phase 3 of `docs/software-plan.md`: what Arty does by itself, and the small perks. Status: **Decided (R14)**. It follows the brainstorm's signature moments (design log section 5: welcome home, company while you work, evening chats) and row 9 (companion first, plus timers and a focus buddy).

## What is already there

- "Welcome home" mostly comes from memory now: every reply knows how long ago the last talk was and what it was about, so "Hey Arty" after a long day gets a fitting answer without a special event.
- The simulator's initiative engine starts one kind of talk by itself ("wants company"), within 5 a day.

## New: timers and the focus buddy

Two new stage directions the AI may write, handled by whoever plays the reply (the simulator page or the body):

| Tag | Example | What happens |
|---|---|---|
| `[timer:DURATION LABEL]` | `[timer:10m tea]`, `[timer:90s]`, `[timer:1h30m oven]` | Starts a timer. Up to 3 at a time, 5 s to 12 h. When it ends, Arty says so (event `timer_done` with the label). |
| `[timer:cancel]` | | Cancels all timers. |
| `[focus:MINUTES]` | `[focus:25]` (default 25, 5 to 90) | Focus buddy: Arty goes quiet beside you (focus face, no self-started talk) for that long, then cheers and suggests a break (event `focus_break`). |
| `[focus:stop]` | | Ends focus mode early. |

The parser turns them into events in the reply stream: `{"type": "timer", "seconds": 600, "label": "tea"}`, `{"type": "timer", "cancel": true}`, `{"type": "focus", "minutes": 25}`, `{"type": "focus", "stop": true}`. The AI is told about them in the prompt, with the rule: only when the person asks for a timer or for help focusing, and always say it out loud too ("Ten minutes for the tea, got it!").

## New: events

`/api/chat` takes an optional `"detail"` (plain text, at most 80 characters, cleaned) that some events use. New events (each is a short instruction to the AI, like `wants_company` today):

| Event | When | Detail |
|---|---|---|
| `timer_done` | A timer ends | The label ("tea"), may be empty |
| `focus_break` | A focus block ends | The minutes |
| `good_morning` | The lights come on for the first time in the morning (05:00 to 11:00) | – |
| `good_night` | The lights go off late (21:00 to 03:00) | – |

Timers and focus breaks always speak (you asked for them). Good morning and good night count against the daily budget of 5 self-started talks, and never happen in focus mode or privacy mode.

## Who does what

- **Mind** (`mind/brain.py`, `mind/server.py`): the two new tags in the parser and the prompt; the new events and `detail`.
- **Simulator** (`sim/index.html`, normal mode): runs timers and focus mode from the reply stream (a small timer chip on screen with the time left and a cancel button), sends the events, good morning and good night from its lights switch, and keeps the meters (energy, boredom, wish for company) across a reload, adjusted for the time that passed.
- **Body** (`body/loop.py`): runs timers and focus mode from the reply stream; when one ends and Arty is idle, it starts the event reply itself (no wake word), otherwise it waits until the current talk ends; a face state `focus`. Good morning and good night wait for a light sensor.
- **Face page**: a `focus` look (calm, half-lidded, steady eyes).

## The body without its mind

Today the body stops if the mind server does not answer. On a robot that is wrong: the body starts anyway, shows the offline face, chirps when woken, and checks the mind every 10 seconds; when it answers, the face goes back to idle. If the mind drops during a talk, the reply ends with the offline face and a sleepy chirp (already so).

## Not in this step

A light sensor, look-to-talk, music, smart-home lights, the initiative engine inside the body.
