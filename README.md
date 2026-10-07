# Milo

A small AI companion robot for the apartment: a face on a screen, a voice, two wheels, and a remote AI as its mind. (Ears and a mood glow are parked until the basics work.) This repository holds the design and the software.

Milo is curious, friendly and a little mischievous. It reacts instantly to what is around it (that part runs on the robot), and it thinks and talks through a remote AI.

The end goal is a physical robot on a Raspberry Pi with a screen, speaker and microphone. The simulator and the mind server in this repository are how we design and test it first.

## Where things are

- `docs/design-log.md`: every decision so far, the open questions and the roadmap. Start here.
- `docs/shopping-lists.md`: what to buy. List A is the first prototype ("Desk Milo", buy now); List B is the final robot (a draft).
- `sim/index.html`: the Milo simulator. Open it in any browser (double-click the file) to play with Milo's face, feelings, shells and life layer. Nothing to install.
- `mind/`: the mind server. It holds the API keys and connects the simulator to DeepSeek (thinking) and Fish Audio (voice).
- `mind/character.md`: Milo's personality as it ships. You edit your own copy from inside the page (see below).
- `start-milo.bat` and `check-keys.bat`: double-click launchers for Windows.
- `.env.example`: the names of the API keys the project uses.
- `data/`: created on your computer. Your personality, its history and the recent conversation live here. Git ignores it.

## Try the simulator (no setup)

1. Open `sim/index.html` in your browser.
2. Poke Milo's face, touch its ears, turn the lights off, switch on privacy mode, or press "Say Hey Milo".
3. Change the shell and the eye colour under "Make it yours".

## Talk to Milo on Windows, step by step

You need Python once, and the project folder. Nothing else.

1. **Install Python.** Go to https://www.python.org/downloads/, press the big yellow button, and run the installer. On its first screen tick **"Add python.exe to PATH"**, then press "Install Now".
2. **Get the project.** Open https://github.com/Samvs1/LIGMA, switch the branch to `main`, press the green **Code** button, then **Download ZIP**. Right-click the ZIP and choose **Extract All**.
3. **Try it without keys.** Open the extracted folder and double-click `start-milo.bat`. If Windows says "Windows protected your PC", press "More info", then "Run anyway". A black window opens (leave it open) and your browser shows Milo with a chat box. Close the black window to stop Milo.
4. **Add your keys.** In the folder, click the address bar at the top of the window, type `cmd` and press Enter. In the black window that opens, type these two lines, pressing Enter after each:
   ```
   copy .env.example .env
   notepad .env
   ```
   Notepad opens. Paste your keys after `DEEPSEEK_API_KEY=` and `FISH_AUDIO_API_KEY=` (no spaces, no quotes), then save and close. Never share this file. Git ignores it.
5. **Test the keys.** Double-click `check-keys.bat`. It says OK or tells you what is wrong (a wrong key, no credit, a blocked network).
6. **Start Milo.** Double-click `start-milo.bat` again. Use Chrome or Edge if you want the microphone button; it needs internet and your permission.

Optional: pick a voice at fish.audio, copy its ID, and put it after `FISH_AUDIO_VOICE_ID=` in `.env` (remove the `#` at the start of that line).

When the project is updated, download the ZIP again and copy your `.env` file and your `data` folder into the new folder.

## Talk to Milo on Mac or Linux

Python 3 is usually there already (`python3 --version`). In a terminal, in this folder:

```
python3 mind/server.py --mock      # a demo with no keys
cp .env.example .env               # then put your keys in .env
python3 mind/server.py --check     # tests the keys with one tiny request each
python3 mind/server.py             # the real thing
```

Then open http://127.0.0.1:8000.

## Editing Milo's personality

Open **Milo's personality** in the page. It is plain words. Change them, then:

- **Try it on some situations** asks Milo seven questions (a bad day, "can you set a timer?", the vacuum cleaner and so on) using the text in the box, even before you save it. Each answer shows the stage directions Milo used and flags problems such as "more than three sentences" or "does not start with an emote". You can change the list of situations.
- **Save** makes it Milo's personality from the next thing it says. No restart. Every save is kept.
- **Earlier versions** takes you back to any save. **Use the shipped default** goes back to the version in the project. Your own versions stay in the list either way.
- **Start over** gives Milo a fresh conversation, which matters when you compare versions, because the old conversation still shows the old style.
- **Copy text** and **Copy conversation** put your personality and the last conversation (with the stage directions) on the clipboard, so you can paste them to Claude and ask for changes.
- **See exactly what Milo is told** shows the whole prompt, so you can see how your words are used.

Your version is saved in `data/character.md` and is never overwritten by updates. The shipped one is `mind/character.md`.

## Run the tests

```
python3 -m unittest discover -s mind/tests -t .
```
On Windows use `py` instead of `python3`.

## Status

Early design stage. Software first (the simulator and the mind server), hardware after: the first hardware is a talking face on a desk (`docs/shopping-lists.md`, List A). See `docs/design-log.md` for the roadmap.
