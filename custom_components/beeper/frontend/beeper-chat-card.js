// Beeper chat card: chat list + conversation + reply box, talking to the beeper integration over HA's WebSocket.
// Message text and titles always go through textContent (never innerHTML): they come from other people.

const NETWORK_ICONS = {
  whatsapp: "mdi:whatsapp", telegram: "mdi:send", signal: "mdi:message-lock", instagram: "mdi:instagram",
  facebook: "mdi:facebook-messenger", messenger: "mdi:facebook-messenger", discord: "mdi:discord", slack: "mdi:slack",
  linkedin: "mdi:linkedin", twitter: "mdi:twitter", x: "mdi:twitter", googlemessages: "mdi:message-text",
  gmessages: "mdi:message-text", imessage: "mdi:apple", beeper: "mdi:alpha-b-circle",
};

const STYLE = `
  :host { display: block; }
  ha-card { display: flex; height: var(--beeper-height, 560px); overflow: hidden; }
  .list { width: 34%; min-width: 200px; border-right: 1px solid var(--divider-color); overflow-y: auto; }
  .conv { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .chat { display: flex; gap: 10px; align-items: center; padding: 10px 12px; cursor: pointer; border-bottom: 1px solid var(--divider-color); }
  .chat:hover, .chat.sel { background: var(--secondary-background-color); }
  .chat ha-icon { color: var(--secondary-text-color); flex: none; --mdc-icon-size: 20px; }
  .meta { flex: 1; min-width: 0; }
  .title { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .preview { color: var(--secondary-text-color); font-size: 0.85em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .badge { background: var(--primary-color); color: var(--text-primary-color); border-radius: 10px; padding: 0 7px; font-size: 0.8em; min-width: 8px; text-align: center; }
  .head { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--divider-color); font-weight: 500; min-height: 24px; }
  .back { display: none; cursor: pointer; }
  .msgs { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 6px; }
  .msg { max-width: 75%; padding: 6px 10px; border-radius: 12px; background: var(--secondary-background-color); white-space: pre-wrap; word-wrap: break-word; }
  .msg.mine { align-self: flex-end; background: var(--primary-color); color: var(--text-primary-color); }
  .sender { font-size: 0.75em; font-weight: 600; opacity: 0.8; }
  .time { font-size: 0.7em; opacity: 0.7; text-align: right; }
  .more { align-self: center; }
  .compose { display: flex; gap: 8px; padding: 8px 12px; border-top: 1px solid var(--divider-color); }
  textarea { flex: 1; resize: none; font: inherit; padding: 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  button { font: inherit; cursor: pointer; border: none; border-radius: 8px; padding: 0 14px; background: var(--primary-color); color: var(--text-primary-color); }
  button:disabled { opacity: 0.5; cursor: default; }
  .empty, .error { margin: auto; color: var(--secondary-text-color); padding: 16px; text-align: center; }
  .error { color: var(--error-color); }
  @media (max-width: 700px) {
    .list { width: 100%; border-right: none; }
    .conv { display: none; }
    ha-card.open .list { display: none; }
    ha-card.open .conv { display: flex; }
    .back { display: inline-block; }
  }
`;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

class BeeperChatCard extends HTMLElement {
  setConfig(config) {
    this._config = { show_archived: false, show_muted: true, ...config };
    if (config.height) this.style.setProperty("--beeper-height", config.height);
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._init();
  }

  getCardSize() { return 10; }
  getGridOptions() { return { columns: 12, rows: 8, min_columns: 6 }; }

  _init() {
    const root = this.attachShadow({ mode: "open" });
    root.appendChild(el("style", null, STYLE));
    this._card = el("ha-card");
    this._list = el("div", "list");
    const conv = el("div", "conv");
    this._head = el("div", "head");
    this._msgs = el("div", "msgs");
    const compose = el("div", "compose");
    this._input = el("textarea");
    this._input.rows = 2;
    this._input.placeholder = "Mensaje…";
    this._send = el("button", null, "Enviar");
    compose.append(this._input, this._send);
    conv.append(this._head, this._msgs, compose);
    this._card.append(this._list, conv);
    root.appendChild(this._card);

    this._send.addEventListener("click", () => this._sendMessage());
    this._input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._sendMessage(); }
    });
    this._renderConversation();
    this._loadChats();
    this._subscribe();
  }

  disconnectedCallback() {
    if (this._unsub) { this._unsub.then((u) => u()); this._unsub = null; }
  }

  connectedCallback() {
    if (this._hass && !this._unsub) { this._subscribe(); this._loadChats(); }
  }

  _subscribe() {
    this._unsub = this._hass.connection.subscribeEvents((ev) => {
      clearTimeout(this._listTimer);
      this._listTimer = setTimeout(() => this._loadChats(), 1000);  // bursts of events → one reload
      if (ev.data.chat_id === this._chatId) {
        clearTimeout(this._msgTimer);
        this._msgTimer = setTimeout(() => this._loadMessages(true), 700);
      }
    }, "beeper_chat_update");
  }

  async _loadChats() {
    try {
      const res = await this._hass.callWS({ type: "beeper/chats" });
      this._chats = res.chats.filter((c) => (this._config.show_archived || !c.archived) && (this._config.show_muted || !c.muted));
      this._available = res.available;
    } catch (err) {
      this._chats = [];
      this._listError = err.message || String(err);
    }
    this._renderList();
  }

  _renderList() {
    this._list.replaceChildren();
    if (this._listError) { this._list.appendChild(el("div", "error", this._listError)); return; }
    if (!this._available) this._list.appendChild(el("div", "error", "Beeper Desktop no responde (¿PC apagado?)"));
    const chats = [...this._chats].sort((a, b) => (b.pinned - a.pinned) || (b.last_activity || "").localeCompare(a.last_activity || ""));
    for (const c of chats) {
      const row = el("div", "chat" + (c.id === this._chatId ? " sel" : ""));
      const icon = el("ha-icon");
      icon.icon = c.pinned ? "mdi:pin" : (NETWORK_ICONS[(c.network || "").toLowerCase().replace(/[^a-z]/g, "")] || "mdi:chat");
      const meta = el("div", "meta");
      meta.append(el("div", "title", c.title), el("div", "preview", c.preview || ""));
      row.append(icon, meta);
      if (c.unread) row.appendChild(el("span", "badge", String(c.unread)));
      row.addEventListener("click", () => this._open(c));
      this._list.appendChild(row);
    }
  }

  async _open(chat) {
    this._chatId = chat.id;
    this._chat = chat;
    this._messages = [];
    this._cursor = null;
    this._card.classList.add("open");
    this._renderList();
    this._renderConversation();
    await this._loadMessages(true);
    if (chat.unread) this._hass.callWS({ type: "beeper/mark_read", chat_id: chat.id }).catch(() => {});
  }

  async _loadMessages(latest) {
    if (!this._chatId) return;
    const chatId = this._chatId;
    try {
      const req = { type: "beeper/messages", chat_id: chatId };
      if (!latest && this._cursor) req.cursor = this._cursor;
      const res = await this._hass.callWS(req);
      if (chatId !== this._chatId) return;  // the user switched chats meanwhile
      if (latest) {
        const older = this._messages.filter((m) => !res.messages.some((n) => n.id === m.id));
        const keepOlder = this._cursor ? older : [];
        this._messages = [...keepOlder, ...res.messages];
        if (!this._cursor) { this._cursor = res.cursor; this._hasMore = res.has_more; }
      } else {
        this._messages = [...res.messages, ...this._messages];
        this._cursor = res.cursor;
        this._hasMore = res.has_more;
      }
      this._msgError = null;
    } catch (err) {
      this._msgError = err.message || String(err);
    }
    this._renderConversation(latest);
  }

  _renderConversation(scrollToEnd = true) {
    this._head.replaceChildren();
    const back = el("ha-icon", "back");
    back.icon = "mdi:arrow-left";
    back.addEventListener("click", () => { this._card.classList.remove("open"); });
    this._head.append(back, el("span", null, this._chat ? this._chat.title : "Beeper"));
    this._msgs.replaceChildren();
    const canSend = this._chat && !this._chat.read_only;
    this._input.disabled = !canSend;
    this._send.disabled = !canSend;
    if (!this._chat) { this._msgs.appendChild(el("div", "empty", "Elige un chat")); return; }
    if (this._msgError) this._msgs.appendChild(el("div", "error", this._msgError));
    if (this._hasMore) {
      const more = el("button", "more", "Cargar anteriores");
      more.addEventListener("click", () => this._loadMessages(false));
      this._msgs.appendChild(more);
    }
    const group = this._chat.type === "group";
    for (const m of this._messages) {
      const b = el("div", "msg" + (m.mine ? " mine" : ""));
      if (group && !m.mine) b.appendChild(el("div", "sender", m.sender || ""));
      b.appendChild(el("div", null, m.text));
      b.appendChild(el("div", "time", fmtTime(m.time) + (m.mine && m.status && m.status !== "SUCCESS" ? " · " + m.status.toLowerCase() : "")));
      this._msgs.appendChild(b);
    }
    if (scrollToEnd) this._msgs.scrollTop = this._msgs.scrollHeight;
  }

  async _sendMessage() {
    const text = this._input.value.trim();
    if (!text || !this._chatId || this._sending) return;
    this._sending = true;
    this._send.disabled = true;
    try {
      await this._hass.callWS({ type: "beeper/send", chat_id: this._chatId, text });
      this._input.value = "";
      setTimeout(() => this._loadMessages(true), 800);
    } catch (err) {
      this._msgError = "No se pudo enviar: " + (err.message || err);
      this._renderConversation();
    } finally {
      this._sending = false;
      this._send.disabled = false;
      this._input.focus();
    }
  }

  static getStubConfig() { return {}; }
}

if (!customElements.get("beeper-chat-card")) {
  customElements.define("beeper-chat-card", BeeperChatCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "beeper-chat-card", name: "Beeper chat", description: "Read and reply to your Beeper chats." });
}
