# Beeper for Home Assistant

Read and reply to your [Beeper](https://www.beeper.com) chats from Home Assistant, using the local
[Beeper Desktop API](https://developers.beeper.com/desktop-api). Beeper Desktop must be running on the computer it points to.

## What you get

- **Sensor `Unread messages`**: total unread messages in chats that are not muted or archived. Its `chats` attribute lists the 20 most recent unread chats.
- **One unread sensor per pinned chat**, with the last message as an attribute.
- **Event `beeper_message`** for each incoming message (`chat`, `chat_id`, `network`, `sender`, `text`, `message_id`). Your own messages do not fire it.
- **Action `beeper.send_message`**: `chat` (title as shown in Beeper, or chat ID) and `message`.
- **Card `custom:beeper-chat-card`**: chat list, conversation and reply box. It loads automatically; there is no resource to add. Options: `height` (default `560px`), `show_archived` (default `false`), `show_muted` (default `true`).

The card and its WebSocket commands are available to admin users only, because they can read and send in every chat.

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

- Attachments show as `[image]`, `[video]`, etc.; only text is displayed and sent.
- The Beeper Desktop API is new and its WebSocket is marked experimental: a Beeper update may break this integration.
