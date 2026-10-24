import importlib.util
import pathlib

# Load api.py directly: importing the package would pull in Home Assistant.
_spec = importlib.util.spec_from_file_location(
    "beeper_api", pathlib.Path(__file__).parents[1] / "custom_components" / "beeper" / "api.py"
)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


def test_summarize_chat_uses_preview_text():
    chat = {"id": "!a:b", "title": "Family", "network": "WhatsApp", "type": "single", "unreadCount": 2,
            "isPinned": True, "preview": {"id": "m1", "type": "IMAGE", "text": ""}}
    s = api.summarize_chat(chat)
    assert s["unread"] == 2 and s["pinned"] and s["preview"] == "[image]" and not s["archived"]


def test_summarize_message_text_and_deleted():
    assert api.summarize_message({"id": "1", "text": "hola", "isSender": True})["mine"]
    assert api.summarize_message({"id": "2", "text": "x", "isDeleted": True})["text"] == "(deleted)"
    assert api.summarize_message({"id": "3", "type": "TEXT", "text": "", "senderID": "@u"})["sender"] == "@u"


def test_chat_ids_are_path_encoded():
    assert api._q("!abc:ba_x.local-whatsapp.localhost") == "%21abc%3Aba_x.local-whatsapp.localhost"


def test_message_attachments_are_listed_and_unsafe_urls_dropped():
    msg = {"id": "7", "type": "IMAGE", "text": "", "attachments": [
        {"type": "img", "srcURL": "localmxc://local/abc", "mimeType": "image/jpeg", "size": {"width": 800, "height": 600}},
        {"type": "video", "id": "mxc://srv/vid", "posterImg": "file:///tmp/p.jpg", "duration": 12},
        {"type": "audio", "srcURL": "mxc://srv/voice", "isVoiceNote": True},
        {"type": "img", "srcURL": "https://evil.example/x.png"},  # not a Beeper media URL: never proxied
    ]}
    s = api.summarize_message(msg)
    assert [a["type"] for a in s["attachments"]] == ["img", "video", "audio"]
    assert s["attachments"][0]["width"] == 800 and s["attachments"][1]["poster"] == "file:///tmp/p.jpg"
    assert s["attachments"][2]["voice"] and s["text"] == ""  # the card shows the media, not "[image]"


def test_message_without_attachments_keeps_placeholder():
    s = api.summarize_message({"id": "8", "type": "IMAGE", "text": ""})
    assert s["attachments"] == [] and s["text"] == "[image]"


def test_voice_mime_allowlist():
    assert api.voice_mime("audio/ogg; codecs=opus") == "audio/ogg"
    assert api.voice_mime("Audio/MP4") == "audio/mp4"
    for bad in (None, "", "audio/wav", "video/webm", "text/html", "../audio/ogg"):
        assert api.voice_mime(bad) is None


class _FakeResp:
    def __init__(self, body):
        self.status, self.content_length, self._body = 200, None, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._body


class _FakeSession:
    def __init__(self, *bodies):
        self.calls, self._bodies = [], list(bodies)

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _FakeResp(self._bodies.pop(0))


def test_send_voice_note_uploads_then_sends_attachment():
    import asyncio

    session = _FakeSession({"uploadID": "u1", "duration": 3.5}, {"pendingMessageID": "p1"})
    client = api.BeeperClient(session, "http://pc:23373/", "t")
    result = asyncio.run(client.send_voice_note("!c:x", b"OggS", "audio/ogg", duration=4))
    assert result == {"pendingMessageID": "p1"}
    (m1, u1, k1), (m2, u2, k2) = session.calls
    assert (m1, u1) == ("POST", "http://pc:23373/v1/assets/upload") and isinstance(k1["data"], api.aiohttp.FormData)
    assert (m2, u2) == ("POST", "http://pc:23373/v1/chats/%21c%3Ax/messages")
    assert k2["json"] == {"attachment": {"uploadID": "u1", "mimeType": "audio/ogg", "fileName": "voice-note.ogg",
                                         "type": "voice-note", "duration": 3.5}}
