# Artemis: open-source inspiration (R10)

A reading list of GitHub projects to borrow ideas from, so memory, behaviour and personality for Arty do not start from zero. Nothing here is chosen or installed. Searched on 8 October 2026; star counts are what the pages showed that day.

**How much to trust each entry.** Marked *checked* means I read the repository's own page. Marked *search only* means I only have search-result summaries or blog posts, so look at the repo yourself before relying on it.

## What Arty has today (for comparison)

The last 12 messages go to the AI with each reply, and the last 40 are saved in a file. There is no long-term memory, no timestamps on memories, and no way to tell people apart. The design log already decides that people's memories stay separate (row 18) and that nicknames live in the conversation (row 32); neither is built. The ideas below fill exactly those gaps.

## 1. Memory

| Project | Licence, size | What to borrow | Fit for Arty |
|---|---|---|---|
| [Mem0](https://github.com/mem0ai/mem0) *(checked)* | Apache-2.0, about 67k stars | Reads a conversation and decides what is worth keeping. Memories are only added, never overwritten. Recall mixes meaning search, keyword search and entity matching, and ranks by time (current state, past events, plans). Self-hostable with `pip` or Docker. | Closest to "remember what I said yesterday". Needs an LLM to do the extracting (defaults to an OpenAI model; the README says others work; I did not check DeepSeek). |
| [Graphiti](https://github.com/getzep/graphiti) (the engine of Zep) *(checked)* | Apache-2.0, about 32k stars | Facts carry a "valid from / until" window. When something changes ("I moved to Tokyo") the old fact is marked outdated, not deleted. | The right idea for "what is true now vs then", but it needs a graph database (Neo4j or similar). Too heavy for a Pi. Borrow the idea, not the install. |
| [Letta](https://github.com/letta-ai/letta) (was MemGPT) *(checked, but its page says little about memory)* | Apache-2.0, about 25k stars | The agent itself edits its own memory, in tiers (always-in-prompt "core" notes, searchable older memory). Blog posts describe it this way; the page did not. | Interesting for "Arty decides what to remember", but it is a whole agent platform. Learning curve is steeper. |
| [Generative Agents](https://github.com/joonspk-research/generative_agents) (Stanford) *(repo checked; the memory design is from the paper, not the page)* | Apache-2.0 | A running log of everything that happened. Recall scores each memory by how recent, how important and how relevant it is. Periodic "reflection" turns many small memories into a few higher-level insights. | This is the model for Arty's planned nightly "dreaming" and "things I have learned about you". |
| [a16z companion-app](https://github.com/a16z-infra/companion-app) *(checked)* | MIT, about 6k stars | Simplest possible pattern: recent chat in the prompt, plus a backstory searched by meaning. A character is one text file (short preamble, a sample chat, free-form backstory). | A pattern to read, not a base to build on: its own README lists known gaps and I could not confirm it is maintained. |
| [CharMemory](https://github.com/bal-spec/sillytavern-character-memory), [MemoryBooks](https://github.com/aikohanasaki/SillyTavern-MemoryBooks), [OpenVault](https://github.com/unkarelian/openvault) for SillyTavern *(search only)* | small community projects | Memories kept as plain, editable text files you can read and correct. OpenVault tracks events, emotions and relationship changes, and who was present for each (so a character does not "know" a secret it never heard). | The editable-file approach fits Arty's planned diary ("see, correct or delete what Arty thinks it knows"). The "who was present" idea fits two people in one home. |

**My suggestion:** do not start with a database. Start with a plain text file of dated facts per person ("things I have learned about you"), write to it after conversations, and let a nightly step summarise the day (the Generative Agents idea). Mem0 is the first tool to try if the plain file stops being enough. Graph databases can wait.

## 2. Whole companions and robots

| Project | What it shows | Notes |
|---|---|---|
| [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) *(checked)* | Voice conversation where you can interrupt (without headphones), a persona set in a config file and a `characters` folder, and the avatar's expressions driven from the backend by an "emotion mapping". About 14k stars, MIT. | The emotion mapping is the same idea as our `[emote:happy]` tags. **Its long-term memory is currently removed** (the page says it will come back), so look here for personality and voice handling, not memory. |
| [Stack-chan](https://github.com/stack-chan/stack-chan) *(search only)* | A small open desk robot with screen, microphones, speaker and head servos, plus community forks that add an AI voice agent. | Closest in spirit to Arty's body; useful for how a tiny robot ties voice, face and servos together. |
| [wire-pod](https://github.com/kercre123/wire-pod) (Anki Vector) and [OpenMoxie](https://appleinsider.com/articles/24/12/20/moxie-robot-may-be-saved-by-a-last-minute-open-sourcing-effort) *(search only)* | What happens when a companion robot depends on a company's servers: Moxie stopped working when its maker shut down in January 2025, and the community had to build replacement servers. | A reason to keep Arty's "two brains" plan (a life layer that works offline) and to keep the character and memory files in your own hands. |
| [awesome-ai-companion](https://github.com/DasterProkio/awesome-ai-companion) and the [ai-companion topic](https://github.com/topics/ai-companion) *(search only)* | Long lists of companion projects, many with memory and "proactive" behaviour. | Good for browsing; quality varies a lot, so check each one. |

## 3. Voice on the robot

| Project | Notes |
|---|---|
| [Pipecat](https://github.com/pipecat-ai/pipecat) *(checked)* | BSD-2, about 16k stars. A Python framework for real-time voice agents with swappable speech-to-text, AI and text-to-speech parts and voice-activity detection. Worth reading for how it handles turn-taking and interruption, and a candidate to replace our own plumbing on the Pi. |
| [computah](https://github.com/jamditis/computah) *(search only)* | MIT, brand new with almost no users. A Raspberry Pi assistant built from openWakeWord, faster-whisper and Piper. Useful as a worked example of the Pi pipeline, not as something to depend on. |
| [Piper](https://github.com/rhasspy/piper) *(checked)* | **Archived and read-only since 6 October 2025.** Development moved to [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) (the name suggests a GPL licence; check it). The design log mentions Piper as the later on-device voice (row 28), so this needs a second look before relying on it. |

One blog reports a fully local Pi 5 voice stack taking 15 to 25 seconds per answer. That is only a blog number, but it supports the current plan of using cloud services for the AI and the voice.

## 4. Recognising people

Arty has no camera in scope (row 37), so voice is the first way to tell people apart.

| Option | What it is | Notes |
|---|---|---|
| [SpeechBrain ECAPA-TDNN](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) *(search only)* | A speaker-recognition model. Apache-2.0. The model card reports a 0.69% error rate on a standard test, compared by a simple similarity score. | Most accurate of the lightweight options. Sizes quoted in sources conflict (15 MB vs 83 MB), so measure the real file. |
| [Resemblyzer](https://pypi.org/project/Resemblyzer/) *(search only)* | Turns a few seconds of speech into a "voiceprint" that you compare to new speech. | Easiest to start with; less accurate. Enrolment takes 5 to 30 seconds per person. Clips under about 1.5 seconds are unreliable. |
| [face_recognition](https://github.com/ageitgey/face_recognition) (dlib) and [InsightFace](https://github.com/deepinsight/insightface) *(search only)* | Face recognition, for when a camera comes back. | `face_recognition` is the simple start. InsightFace is more accurate but its ready-made models are for non-commercial use only. I found no Pi 5 benchmark for either. |

None of this has been tested on a Pi. Whatever is chosen, voiceprints and face data are biometric data: keep them on the robot or your own computer, and get each person's say-so (the design log already says memory is private by default, row 18).

## What I did and did not check

Read directly on their repository pages: Mem0, Graphiti, Letta (little detail), Generative Agents (no memory detail), companion-app, Open-LLM-VTuber, Pipecat, Piper. Everything marked "search only" comes from search-result summaries and should be confirmed on the repo before it influences a decision. Licence names are as shown on the pages and are worth re-checking before any commercial use.

## Possible next steps (your call)

1. Decide the memory approach: the plain dated-facts file (my suggestion), or Mem0.
2. Decide whether the voice-ID work comes before or after the first real voice run.
3. Look at Open-LLM-VTuber's character folder and Pipecat's turn-taking, since both overlap with what the mind server already does.
