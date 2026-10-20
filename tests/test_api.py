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
