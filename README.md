# Milo

A small AI companion robot for the apartment: a face on a screen, ears, a mood glow, two wheels, and a remote AI as its mind. This repository holds the design and the software.

Milo is curious, friendly and a little mischievous. It reacts instantly to what is around it (that part runs on the robot), and it thinks and talks through a remote AI.

## Where things are

- `docs/design-log.md`: every decision so far, the open questions and the roadmap. Start here.
- `sim/index.html`: the Milo simulator. Open it in any browser (double-click the file) to play with Milo's face, feelings, shells and life layer. Nothing to install.
- `mind/`: the mind server. It holds the API keys and connects the simulator to DeepSeek (thinking) and Fish Audio (voice).
- `mind/character.md`: Milo's personality in plain words. Edit it, save it, and the next reply changes.
- `docs/img/`: concept images.
- `.env.example`: the names of the API keys the project uses.

## Try the simulator (no setup)

1. Open `sim/index.html` in your browser.
2. Poke Milo's face, touch its ears, turn the lights off, switch on privacy mode, or press "Say Hey Milo".
3. Change the shell and the eye colour under "Make it yours".

## Talk to Milo (the mind server)

You need Python 3, which is already on most Mac and Linux computers. On Windows, install it from python.org. Check with `python3 --version` (on Windows try `py --version`). Nothing else needs installing.

### Step 1: try it without any keys

Open a terminal in this folder and run:

```
python3 mind/server.py --mock
```

Open http://127.0.0.1:8000 in your browser. The page now has a chat box that works with a small demo mind and a babble voice. Press Ctrl+C in the terminal to stop.

### Step 2: use the real mind and voice

1. Copy `.env.example` to a new file called `.env` (same folder).
2. Open `.env` in a text editor and paste your keys after `DEEPSEEK_API_KEY=` and `FISH_AUDIO_API_KEY=`. Do not share this file and do not commit it. Git ignores it.
3. Optional: pick a voice at fish.audio, copy its ID, and paste it after `FISH_AUDIO_VOICE_ID=` (remove the `#` at the start of that line).
4. Test the keys with one tiny request each: `python3 mind/server.py --check`
5. Start it: `python3 mind/server.py` and open http://127.0.0.1:8000.

If something fails, the messages say what is wrong (a wrong key, no credit, a blocked network). Use Chrome, Edge or Safari for the microphone button; it needs internet and your permission.

### Run the tests

```
python3 -m unittest discover -s mind/tests -t .
```

## Status

Early design stage. Software first (the simulator and the mind server), hardware after. See `docs/design-log.md` for the roadmap.
