"""Talks, times and memory in the conversation (mind/brain.py, Mind), with a stand-in memory store."""
import unittest
from datetime import datetime, timedelta, timezone

from mind.brain import DemoBrain, Mind, ago, split_talks

TZ = timezone(timedelta(hours=2))
T0 = datetime(2026, 10, 8, 18, 0, tzinfo=TZ)


def msg(role, content, minutes, **extra):
    return {"role": role, "content": content, "time": (T0 + timedelta(minutes=minutes)).isoformat(), **extra}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class RecordingBrain(DemoBrain):
    """The demo mind, remembering the messages it was sent."""

    def __init__(self):
        self.sent = []

    def stream(self, messages):
        self.sent.append(messages)
        yield from super().stream(messages)


class FakeStore:
    def __init__(self, facts=(), episodes=(), state=None, broken=False):
        self._facts, self._episodes, self._state, self.broken = list(facts), list(episodes), dict(state or {}), broken
        self.used = []

    def recall(self, query, k=8, core_limit=6):
        if self.broken:
            raise OSError("disk on fire")
        return list(self._facts)

    def render(self, facts, episodes, now=None):
        lines = [f"- {f['text']}" for f in facts] + [f"- talk: {e['summary']}" for e in episodes]
        return "## What you remember about them\n" + "\n".join(lines) if lines else ""

    def episodes(self, limit=None):
        return self._episodes[-limit:] if limit else list(self._episodes)

    def state(self):
        return dict(self._state)

    def set_state(self, **values):
        self._state.update(values)

    def mark_used(self, ids):
        self.used.extend(ids)


class SplitTests(unittest.TestCase):
    def test_quiet_of_more_than_45_minutes_starts_a_new_talk(self):
        talks = split_talks([msg("user", "a", 0), msg("assistant", "b", 1), msg("user", "c", 50), msg("assistant", "d", 51)])
        self.assertEqual([[m["content"] for m in t] for t in talks], [["a", "b"], ["c", "d"]])

    def test_messages_without_a_time_are_one_old_talk(self):
        talks = split_talks([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}, msg("user", "z", 0)])
        self.assertEqual([len(t) for t in talks], [2, 1])

    def test_ago_reads_naturally(self):
        self.assertEqual(ago(timedelta(minutes=50)), "about 50 minutes ago")
        self.assertEqual(ago(timedelta(hours=5)), "about 5 hours ago")
        self.assertEqual(ago(timedelta(days=2, hours=1)), "2 days ago")


class ContextTests(unittest.TestCase):
    def mind(self, history, store=None, minutes_now=0):
        brain = RecordingBrain()
        m = Mind(brain, memory=store, clock=Clock(T0 + timedelta(minutes=minutes_now)))
        m.history = list(history)
        return m, brain

    def system_and_past(self, brain):
        sent = brain.sent[-1]
        return sent[0]["content"], sent[1:-1]

    def test_only_the_current_talk_goes_with_the_reply(self):
        old = [msg("user", "yesterday's news", -1440), msg("assistant", "oh nice", -1439)]
        now = [msg("user", "hi again", 0), msg("assistant", "hello!", 1)]
        m, brain = self.mind(old + now, FakeStore(state={"noted_until": (T0 - timedelta(minutes=1439)).isoformat()}), minutes_now=2)
        list(m.chat("how are you", "", {}))
        system, past = self.system_and_past(brain)
        self.assertEqual([p["content"] for p in past], ["hi again", "hello!"])
        self.assertIn("you last talked about 23 hours ago", system)
        self.assertNotIn("not yet in your memory", system)          # already noted by the keeper

    def test_a_talk_the_keeper_has_not_noted_yet_is_bridged(self):
        old = [msg("user", "my sister visits on Friday", -120), msg("assistant", "[emote:happy] How lovely!", -119)]
        m, brain = self.mind(old, FakeStore(), minutes_now=0)
        list(m.chat("hello", "", {}))
        system, past = self.system_and_past(brain)
        self.assertEqual(past, [])
        self.assertIn("The end of your last talk (not yet in your memory)", system)
        self.assertIn("They: my sister visits on Friday", system)
        self.assertIn("You: How lovely!", system)                   # stage directions removed

    def test_first_conversation_is_said_so(self):
        m, brain = self.mind([], FakeStore())
        list(m.chat("hello", "", {}))
        self.assertIn("first conversation you remember", self.system_and_past(brain)[0])

    def test_remembered_facts_go_in_the_prompt_and_are_marked_used(self):
        store = FakeStore(facts=[{"id": "f_1", "text": "They love sunbeams."}],
                          episodes=[{"summary": "We watched the rain.", "end": (T0 - timedelta(days=1)).isoformat()}])
        m, brain = self.mind([], store)
        list(m.chat("hello", "", {}))
        system = self.system_and_past(brain)[0]
        self.assertIn("They love sunbeams.", system)
        self.assertIn("We watched the rain.", system)
        self.assertIn("you last talked about 24 hours ago", system)   # from the last episode, as nothing is in the history
        self.assertEqual(store.used, ["f_1"])

    def test_the_morning_thought_is_offered_once_on_the_first_talk_of_the_day(self):
        store = FakeStore(state={"morning_thought": {"for_date": "2026-10-08", "text": "Ask about the exam.", "used": False}})
        m, brain = self.mind([], store)
        list(m.chat("good morning", "", {}))
        self.assertIn("Ask about the exam.", self.system_and_past(brain)[0])
        self.assertTrue(store.state()["morning_thought"]["used"])
        list(m.chat("and now?", "", {}))
        self.assertNotIn("Ask about the exam.", self.system_and_past(brain)[0])

    def test_events_are_marked_so_the_keeper_can_skip_them(self):
        m, _ = self.mind([], FakeStore())
        list(m.chat("", "wants_company", {}))
        self.assertEqual(m.history[0].get("event"), "wants_company")
        self.assertNotIn("event", m.history[1])

    def test_memory_trouble_never_stops_a_reply(self):
        m, brain = self.mind([msg("user", "hi", 0)], FakeStore(broken=True), minutes_now=1)
        events = list(m.chat("hello", "", {}))
        self.assertTrue(any(e["type"] == "say" for e in events))
        self.assertEqual(events[-1]["type"], "done")

    def test_without_a_memory_store_the_last_talk_is_still_bridged(self):
        old = [msg("user", "remember the plant", -200)]
        m, brain = self.mind(old, None)
        list(m.chat("hi", "", {}))
        self.assertIn("They: remember the plant", self.system_and_past(brain)[0])


if __name__ == "__main__":
    unittest.main()
