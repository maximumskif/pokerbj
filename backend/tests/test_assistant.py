import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from app import assistant as mod
from app.assistant import ScreenAssistant, is_useful_comment, visible_text
from app.brain import Brain
from app.capture import frame_signature


class FakeClaude:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.calls = []
        self.model = "claude-opus-5-5"
        self.use_fallbacks = True
        self.has_key = True

    def client(self):
        outer = self

        class Msgs:
            async def create(self, **kw):
                outer.calls.append(kw)
                return SimpleNamespace(stop_reason="end_turn",
                                       content=[SimpleNamespace(type="text", text=outer.replies.pop(0))])

        return SimpleNamespace(beta=SimpleNamespace(messages=Msgs()))


class FakeSource:
    label = "Fake window"

    def __init__(self):
        self.frame = np.zeros((300, 400, 3), np.uint8)

    def grab(self):
        return self.frame.copy(), None

    def close(self):
        pass


class FakeOllama:
    """Mock Ollama server: records request bodies, answers chat (streamed) and background looks (JSON)."""

    def __init__(self, monkeypatch, replies=(), looks=()):
        self.bodies, self.replies, self.looks = [], list(replies), list(looks)
        transport = httpx.MockTransport(self.handle)
        real = httpx.AsyncClient
        monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
        monkeypatch.setattr(mod.httpx, "get", lambda url, **kw: httpx.Client(transport=transport).get(url))

    def handle(self, request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": mod.OLLAMA_MODEL}]})
        body = json.loads(request.content)
        self.bodies.append(body)
        if body.get("format"):
            return httpx.Response(200, json={"message": {"content": json.dumps(self.looks.pop(0))}, "done": True})
        if not body["stream"]:
            return httpx.Response(200, json={"message": {"content": "x"}, "done": True})
        text = self.replies.pop(0)
        lines = [{"message": {"content": text[:5]}}, {"message": {"content": text[5:]}}, {"done": True}]
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())


def make_claude(replies):
    claude = FakeClaude(replies)
    a = ScreenAssistant(claude, provider="claude", brain=Brain())
    a.source = FakeSource()
    return a, claude


def make_local(monkeypatch, replies=(), looks=()):
    fake = FakeOllama(monkeypatch, replies, looks)
    a = ScreenAssistant(FakeClaude(), provider="ollama", brain=Brain())
    a.source = FakeSource()
    return a, fake


# ---------------------------------------------------------------- chat basics (Claude path)
def test_chat_sends_only_latest_screenshot_and_alternates_roles():
    a, claude = make_claude(["first answer", "second answer"])
    asyncio.run(a.ask("what is this?"))
    asyncio.run(a.ask("and now?"))
    msgs = claude.calls[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    images = [b for m in msgs if isinstance(m["content"], list) for b in m["content"] if b["type"] == "image"]
    assert len(images) == 1 and images[0]["source"]["media_type"] == "image/jpeg"
    assert msgs[1]["content"] == "first answer"
    assert claude.calls[1]["fallbacks"] == "default"
    assert [m["text"] for m in a.log if m["role"] != "system"] == ["what is this?", "first answer", "and now?", "second answer"]


def test_needs_key_and_target():
    a, claude = make_claude([])
    claude.has_key = False
    with pytest.raises(ValueError):
        asyncio.run(a.ask("hi"))
    claude.has_key = True
    a.source = None
    with pytest.raises(ValueError):
        asyncio.run(a.ask("hi"))


def test_chatbox_picker_lists_monitors_and_windows_but_not_itself():
    import chatbox
    opts = chatbox.target_options({"monitors": [{"index": 1, "rect": [0, 0, 1920, 1080]}],
                                   "windows": [{"hwnd": 5, "title": "Excel"}, {"hwnd": 6, "title": chatbox.TITLE}, {"hwnd": 7, "title": ""}]})
    assert [o[0] for o in opts] == ["Whole monitor 1", "Excel"]
    assert opts[0][1] == {"kind": "region", "rect": [0, 0, 1920, 1080], "title": "Monitor 1"}


# ---------------------------------------------------------------- local model
def test_local_model_streams_quick_look_with_screenshot(monkeypatch):
    a, fake = make_local(monkeypatch, replies=["Fix line 3.\nNext: rerun the tests."])
    asyncio.run(a.ask(""))
    msgs = fake.bodies[-1]["messages"]
    assert msgs[0]["role"] == "system" and msgs[-1]["images"] and msgs[-1]["content"].startswith("Quick look")
    assert a.log[-1]["text"] == "Fix line 3.\nNext: rerun the tests." and "streaming" not in a.log[-1]
    assert a.last_latency_ms["total"] >= a.last_latency_ms["first_token"] >= 0


def test_not_running_explains_what_to_do():
    a = ScreenAssistant(FakeClaude(), provider="ollama", brain=Brain())
    a.source = FakeSource()
    mod_url = mod.OLLAMA_URL
    try:
        mod.OLLAMA_URL = "http://127.0.0.1:9"  # nothing listens here
        with pytest.raises(ValueError, match="Ollama"):
            asyncio.run(a.ask("hi"))
    finally:
        mod.OLLAMA_URL = mod_url
    assert [e for e in a.log if e["role"] != "system"] == []


def test_preread_screenshot_is_reused_by_the_next_question(monkeypatch):
    a, fake = make_local(monkeypatch, replies=["ok"])
    frame = a.source.frame.copy()
    a._fresh = (1e18, frame)
    asyncio.run(a._preread_frame(frame, frame_signature(frame), a._prefix_key()))
    asyncio.run(a.ask("what now?"))
    pre, ask = fake.bodies
    assert pre["options"]["num_predict"] == 1 and pre["messages"][-1]["content"] == ""
    assert ask["messages"][-1]["images"] == pre["messages"][-1]["images"]      # same image -> cached by Ollama
    assert ask["messages"][:-1] == pre["messages"][:-1]                        # same prefix
    assert pre["options"]["num_ctx"] == ask["options"]["num_ctx"]              # no model reload
    assert a.last_latency_ms["pre_read"] is True


# ---------------------------------------------------------------- second brain
def test_background_look_saves_memory_and_posts_new_tips_only(monkeypatch):
    look = {"seen": "VS Code: app.py, TypeError on line 42 in parse_amount", "comment": "Line 42 passes a str to int(); strip the $ first."}
    a, fake = make_local(monkeypatch, looks=[look, look])
    assert not a.watching  # off until the user turns it on
    a.watching = True
    note = asyncio.run(a.observe(a.source.frame))
    assert note["watch"] and "Line 42" in note["text"]
    assert a.brain.recent_observations()[-1][1].startswith("VS Code: app.py")
    assert fake.bodies[0]["format"]["required"] == ["seen", "comment"]
    assert asyncio.run(a.observe(a.source.frame)) is None                     # same tip again: stays quiet
    assert "Your recent comments" in fake.bodies[1]["messages"][-1]["content"]
    assert len(a.brain.recent_observations()) == 1                            # near-duplicate note not stored twice
    a.watching = False
    fake.looks.append({"seen": "Excel budget sheet, total 4,210", "comment": "Cell B7 formula skips row 6."})
    assert asyncio.run(a.observe(a.source.frame)) is None                     # tips off: memory only
    assert a.brain.recent_observations()[-1][1] == "Excel budget sheet, total 4,210"


def test_questions_get_memory_after_the_screenshot(monkeypatch):
    a, fake = make_local(monkeypatch, replies=["It was 4,210."])
    a.brain.add_observation("Excel budget sheet, total 4,210", t=time.time() - 60)
    a.brain.add_observation("Chrome: flight to Lisbon, $389 on TAP", t=time.time() - 30 * 3600)
    asyncio.run(a.ask("what was the flight price I saw earlier?"))
    user = fake.bodies[-1]["messages"][-1]
    assert "Excel budget sheet, total 4,210" in user["content"]               # recent memory
    assert "Lisbon, $389" in user["content"]                                  # older note found by search
    assert "[Question]\nwhat was the flight price I saw earlier?" in user["content"]
    assert "4,210" not in fake.bodies[-1]["messages"][0]["content"]           # memory isn't in the cached system prefix


def test_remember_lines_become_facts_and_are_hidden(monkeypatch):
    a, fake = make_local(monkeypatch, replies=["Noted, good luck with it.\nREMEMBER: User is learning Python for a data job.",
                                               "Try a list comprehension."])
    asyncio.run(a.ask("remember that I'm learning python for a data job"))
    assert a.log[-2]["text"] == "Noted, good luck with it."
    assert a.log[-1]["text"].startswith("📌 Remembered")
    assert a.brain.facts()[0][1] == "I'm learning python for a data job."
    asyncio.run(a.ask("how do I make this loop shorter?"))
    assert "I'm learning python for a data job." in fake.bodies[-1]["messages"][0]["content"]
    asyncio.run(a.ask("/facts"))
    assert "learning python" in a.log[-1]["text"]
    asyncio.run(a.ask("/forget 1"))
    assert a.brain.facts() == []


def test_deep_mode_asks_for_reasoning_and_a_recommendation(monkeypatch):
    a, fake = make_local(monkeypatch, replies=["- a\n- b\nDo this: c"])
    asyncio.run(a.ask("should I take this offer?", deep=True))
    body = fake.bodies[-1]
    assert "Do this:" in body["messages"][-1]["content"] and body["options"]["num_predict"] > 400
    assert a.log[-2]["text"].startswith("🧠")


def test_chat_survives_restart():
    brain = Brain()
    claude = FakeClaude(["hello back"])
    a = ScreenAssistant(claude, provider="claude", brain=brain)
    a.source = FakeSource()
    asyncio.run(a.ask("hello"))
    b = ScreenAssistant(claude, provider="claude", brain=brain)
    assert [e["text"] for e in b.log] == ["hello", "hello back"]


def test_visible_text_hides_remember_line_even_mid_stream():
    assert visible_text("Sure.\nREMEMBER: User likes short answers.") == "Sure."
    assert visible_text("Sure.\nREMEM") == "Sure."
    assert visible_text("Sure.\n**REMEMBER:** User likes tea") == "Sure."
    assert visible_text("Remember to save your file first.") == "Remember to save your file first."


def test_inline_remember_is_saved_and_hidden(monkeypatch):
    a, fake = make_local(monkeypatch, replies=["Got it, blunt it is. REMEMBER: User prefers blunt, short answers.",
                                               "Fix the import. REMEMBER: Focus on optimizing shell commands.",
                                               "Nice. REMEMBER: <the fact as one short sentence about the user>"])
    asyncio.run(a.ask("I like blunt answers"))
    assert a.log[-2]["text"] == "Got it, blunt it is."
    assert a.brain.facts()[0][1] == "User prefers blunt, short answers."
    asyncio.run(a.ask(""))                                   # quick look: the model can't invent facts
    asyncio.run(a.ask("I just got a new job"))               # template placeholder is never saved
    assert [f for _, f in a.brain.facts()] == ["User prefers blunt, short answers."]
    assert a.log[-1]["text"] == "Nice."


@pytest.mark.parametrize("text", [
    "Ensure all tests pass before applying changes to avoid issues.",
    "Make sure to save your work.",
    "Stay focused on the task at hand.",
    "Keep an eye on the shell output.",
    "Consider reviewing the guide for more methods.",
    "You're watching a YouTube video about true crime.",
    "Great progress so far!",
    "Review the guide to implement these methods.",
])
def test_generic_comments_are_dropped(text):
    assert not is_useful_comment(text)


@pytest.mark.parametrize("text", [
    "Line 88 calls parse_amount() before the None check, so empty rows will crash it.",
    "That flight is $389 here but was $342 on the earlier tab; book the earlier one.",
    "Your B12 total stops at B10, so rows 11-12 aren't counted.",
    "The 'Delete account' button is the red one, not Save.",
    "Your git push is going to origin/main, not your feature branch.",
])
def test_specific_comments_are_kept(text):
    assert is_useful_comment(text)


def test_generic_live_comment_is_not_posted(monkeypatch):
    a, fake = make_local(monkeypatch, looks=[{"seen": "Terminal running pytest", "comment": "Ensure all tests pass before deploying."}])
    assert asyncio.run(a.observe(a.source.frame)) is None
    assert a.brain.recent_observations()[-1][1] == "Terminal running pytest"     # still remembered


def test_vague_and_commentary_commands():
    from app.assistant import _VAGUE, _WATCH_ON, _WATCH_OFF, FRESH_TAKE, ScreenAssistant
    from app.brain import Brain
    assert _VAGUE.match("insight") and _VAGUE.match("give insight") and _VAGUE.match("??")
    assert not _VAGUE.match("why is line 4 failing")
    assert _WATCH_ON.search("i want live commentary") and _WATCH_OFF.search("stop live commentary")
    a = ScreenAssistant(None, provider="ollama", brain=Brain())
    assert FRESH_TAKE in a._user_text("hi")


def test_goes_quiet_during_live_chess(monkeypatch):
    from app.assistant import PAUSED_MSG, live_game_title
    assert live_game_title(["Play Chess Online for FREE - 2 Player Chess - Google Chrome"])
    assert live_game_title(["wemma0011 vs maximumski - lichess.org"])
    assert not live_game_title(["Game Review - Chess.com", "Analysis Board - lichess.org", "Excel"])
    a, fake = make_local(monkeypatch, replies=["should not be used"])
    a.window_titles = lambda: ["Play Chess Online for FREE - Chrome"]
    res = asyncio.run(a.ask("what move should I play?"))
    assert res["text"] == PAUSED_MSG and not fake.bodies
    a.window_titles = lambda: ["Visual Studio Code"]
    a._paused_checked = 0
    assert not a.check_paused() and a.log[-1]["text"].startswith("Live chess game closed")
