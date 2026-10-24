# Beeper for Home Assistant

Read and reply to your [Beeper](https://www.beeper.com) chats from Home Assistant, using the local
[Beeper Desktop API](https://developers.beeper.com/desktop-api). Beeper Desktop must be running on the computer it points to.

## What you get

- **Sensor `Unread messages`**: total unread messages in chats that are not muted or archived. Its `chats` attribute lists the 20 most recent unread chats.
- **One unread sensor per pinned chat**, with the last message as an attribute.
- **Event `beeper_message`** for each incoming message (`chat`, `chat_id`, `network`, `sender`, `text`, `message_id`). Your own messages do not fire it.
- **Action `beeper.send_message`**: `chat` (title as shown in Beeper, or chat ID) and `message`.
- **Card `custom:beeper-chat-card`**: chat list, conversation and reply box. It loads automatically; there is no resource to add. Options: `height` (default `560px`), `show_archived` (default `false`), `show_muted` (default `true`).
  Images, GIFs, stickers, videos and voice notes show inside the messages (a tap on an image opens it full size), and
  web addresses are clickable. Formatting shows too: bold, italic, underline, strikethrough, code, quotes and lists,
  from HTML messages (only those tags are rebuilt, without attributes, and only http/https links; scripts, images,
  styles and frames inside a message are dropped) and from WhatsApp-style `*bold*` `_italic_` `~strike~` `` `code` ``.
  Instagram reel/post links get a "▶ Ver reel" button that plays the video inline when a helper resolves the public
  video file (optional: an entity `sensor.vault_images` with `key` and `lan` attributes, serving
  `/vault-img/<key>/reel/<code>`; for example
  [vault_files_server.py](https://github.com/mauriporte/homelab-ha-fullinstall/tree/main/modules/notes-vault-patches)). Without it the link just opens Instagram.
  Google Maps links show a small map under the message, like WhatsApp (Google's keyless embed, loaded as it scrolls
  into view; a tap opens Google Maps) (share
  links like maps.app.goo.gl need the same helper, at `/vault-img/<key>/maps?u=<link>`).

The card and its WebSocket commands are available to admin users only, because they can read and send in every chat.
Media go through `/api/beeper/asset`, also admin-only and loaded with short-lived signed URLs; it only serves
attachments of messages the card has loaded (never arbitrary files from the PC running Beeper Desktop), and sends them
with a sandbox Content-Security-Policy.

**Voice notes (`POST /api/beeper/voice`, admin only)**, for a watch or phone app that records audio. Send
`multipart/form-data` with `chat_id` (required; must be a chat the integration knows), `file` (required, the audio
bytes, at most 5 MB), `duration` (optional, seconds) and `mime` (optional; else the file part's type). Allowed types:
`audio/ogg`, `audio/opus`, `audio/webm`, `audio/mp4`, `audio/aac`, `audio/mpeg`. The file is uploaded to Beeper Desktop
and sent as a voice note, without text. Answer: `{"chat_id": ..., "pending_message_id": ...}`; errors are
`{"message": ..., "code": ...}` with `invalid_request` / `unsupported_media` (400), `forbidden` (403),
`unknown_chat` (404), `too_large` (413), `beeper_error` / `beeper_auth_failed` (502) or `beeper_unavailable` (503,
Beeper Desktop not running).

```sh
curl -X POST https://<home assistant>/api/beeper/voice -H "Authorization: Bearer <admin token>" \
  -F chat_id='<chat id>' -F duration=4.2 -F 'file=@note.ogg;type=audio/ogg'
```

## Setup

1. In Beeper Desktop: **Settings → Developers**. Enable the Desktop API and create an access token with read and write access.
2. Make the API reachable from Home Assistant. It listens on `127.0.0.1:23373` only. On Windows, forward the port and allow only Home Assistant's IP (run as administrator):

   ```powershell
   netsh interface portproxy add v4tov4 listenaddress=<PC IP> listenport=23373 connectaddress=127.0.0.1 connectport=23373
   New-NetFirewallRule -DisplayName "Beeper API (Home Assistant only)" -Direction Inbound -Protocol TCP -LocalAddress <PC IP> -LocalPort 23373 -RemoteAddress <Home Assistant IP> -Action Allow
   ```

3. Copy `custom_components/beeper` into your Home Assistant `config/custom_components`, restart, then add the **Beeper** integration with `http://<PC IP>:23373` and the token.

## Example automation

```yaml
triggers:
  - trigger: event
    event_type: beeper_message
    event_data:
      chat: Family
actions:
  - action: light.turn_on
    target:
      entity_id: light.living_room
    data:
      flash: short
```

## Limits

- Only text and voice notes can be sent from Home Assistant; other attachments are shown, not sent. Media need Beeper
  Desktop running (they stream from it).
- The Beeper Desktop API is new and its WebSocket is marked experimental: a Beeper update may break this integration.
